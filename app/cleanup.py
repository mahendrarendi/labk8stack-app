"""Remove sandbox namespaces belonging to users who have been inactive.

Each user and challenge pair gets a namespace. Retries keep that namespace to
avoid slow deletion and recreation. A sandbox can remain after a challenge is
passed or expires; this module removes it.

Examples::

    # Preview the cleanup plan (the default is a dry run).
    LAB_MODE=kubernetes LAB_KUBECONFIG=$HOME/.kube/config \
        .venv/bin/python -m app.cleanup --idle-days 30

    # Delete the selected namespaces.
    LAB_MODE=kubernetes LAB_KUBECONFIG=$HOME/.kube/config \
        .venv/bin/python -m app.cleanup --idle-days 30 --apply

Only namespaces labeled ``k8s-lab.io/component=sandbox`` are affected.
Namespaces whose users no longer exist in the database are also selected,
unless ``--keep-orphans`` is specified.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import json
from datetime import datetime, timezone
from shutil import which

from . import db
from .config import settings

SANDBOX_LABEL = "k8s-lab.io/component=sandbox"
EXPIRY_ANNOTATION = "k8s-lab.io/expires-at"


def _kubectl(args: list[str], timeout: int = 60) -> tuple[bool, str]:
    if which("kubectl") is None:
        raise SystemExit("error: kubectl was not found in PATH.")
    env = os.environ.copy()
    if settings.kubeconfig:
        env["KUBECONFIG"] = settings.kubeconfig
    proc = subprocess.run(
        ["kubectl", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, output.strip()


def list_sandbox_namespaces() -> list[str]:
    ok, output = _kubectl(
        ["get", "namespaces", "-l", SANDBOX_LABEL, "-o", "name"]
    )
    if not ok:
        raise SystemExit(f"error: failed to list namespaces: {output}")
    names = []
    for line in output.splitlines():
        name = line.strip().split("/", 1)[-1]
        if name:
            names.append(name)
    return sorted(names)


def expired_sandbox_namespaces(now: datetime | None = None) -> list[str]:
    """Find abandoned attempts using the expiry set when the sandbox starts."""
    ok, output = _kubectl(["get", "namespaces", "-l", SANDBOX_LABEL, "-o", "json"])
    if not ok:
        raise SystemExit(f"error: failed to list namespaces: {output}")
    now = now or datetime.now(timezone.utc)
    expired = []
    for item in json.loads(output).get("items", []):
        metadata = item.get("metadata") or {}
        name = metadata.get("name", "")
        value = (metadata.get("annotations") or {}).get(EXPIRY_ANNOTATION)
        if not value or parse_user_id(name) is None:
            continue
        try:
            deadline = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if deadline.tzinfo and deadline <= now:
                expired.append(name)
        except ValueError:
            continue
    return sorted(expired)


def parse_user_id(namespace: str) -> int | None:
    pattern = rf"^{re.escape(settings.namespace_prefix)}(\d+)-"
    match = re.match(pattern, namespace)
    return int(match.group(1)) if match else None


def completed_namespace_names(
    namespaces: list[str], completed: set[tuple[int, str]]
) -> list[str]:
    names = []
    for namespace in namespaces:
        user_id = parse_user_id(namespace)
        if user_id is None:
            continue
        prefix = f"{settings.namespace_prefix}{user_id}-"
        if (user_id, namespace[len(prefix):]) in completed:
            names.append(namespace)
    return sorted(names)


def plan_cleanup(
    namespaces: list[str],
    known_user_ids: set[int],
    selected_user_ids: set[int],
) -> dict[str, list[str]]:
    """Group namespaces selected for deletion.

    ``idle``   = belongs to a user selected for inactivity
    ``orphan`` = belongs to a user no longer present in the database
    """
    plan: dict[str, list[str]] = {"idle": [], "orphan": []}
    for namespace in namespaces:
        user_id = parse_user_id(namespace)
        if user_id is None:
            continue
        if user_id not in known_user_ids:
            plan["orphan"].append(namespace)
        elif user_id in selected_user_ids:
            plan["idle"].append(namespace)
    for names in plan.values():
        names.sort()
    return plan


def _activity(user) -> str:
    if "activity" in user.keys():
        return user["activity"] or "-"
    return user["last_seen"] or user["created_at"]


def _describe(users: list) -> None:
    for user in users:
        print(
            f"  - id={user['id']} {user['name']} "
            f"(last activity: {_activity(user)})"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cleanup",
        description="Remove sandbox namespaces belonging to inactive users.",
    )
    parser.add_argument(
        "--idle-days",
        type=int,
        default=settings.sandbox_idle_days,
        help=f"inactivity threshold in days (default: {settings.sandbox_idle_days})",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="select all users, including active users",
    )
    parser.add_argument(
        "--keep-orphans",
        action="store_true",
        help="keep namespaces whose users no longer exist in the database",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="delete selected namespaces; without this flag, only show the plan",
    )
    args = parser.parse_args(argv)

    if settings.mode != "kubernetes":
        print(
            "error: cleanup requires LAB_MODE=kubernetes "
            f"(current mode: {settings.mode}).",
            file=sys.stderr,
        )
        return 2

    users = db.all_users()
    known = {user["id"] for user in users}

    if args.all:
        selected_rows = users
    else:
        selected_rows = db.idle_users(args.idle_days)
    selected = {user["id"] for user in selected_rows}

    namespaces = list_sandbox_namespaces()
    plan = plan_cleanup(namespaces, known, selected)
    if args.keep_orphans:
        plan["orphan"] = []

    expired = expired_sandbox_namespaces()
    completed = completed_namespace_names(namespaces, db.completed_sandboxes())
    targets = sorted(set(plan["idle"] + plan["orphan"] + expired + completed))
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] selected users: {len(selected_rows)}")
    if selected_rows:
        _describe(selected_rows)
    print(
        f"sandbox namespaces found: {len(namespaces)} | "
        f"selected for deletion: {len(targets)} "
        f"(idle={len(plan['idle'])}, orphan={len(plan['orphan'])}, "
        f"expired={len(expired)}, completed={len(completed)})"
    )
    for namespace in targets:
        print(f"  - {namespace}")

    if not targets:
        return 0
    if not args.apply:
        print("Run again with --apply to delete these namespaces.")
        return 0

    failures = 0
    for namespace in targets:
        ok, output = _kubectl(
            [
                "delete", "namespace", namespace,
                "--ignore-not-found", "--wait=false",
            ]
        )
        status = "ok" if ok else "failed"
        print(f"delete {namespace}: {status}")
        if not ok:
            failures += 1
            print(f"  {output}")
    print(
        "Deletion is asynchronous; namespaces may remain Terminating "
        "for several minutes."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
