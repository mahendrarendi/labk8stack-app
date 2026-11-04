"""Real-cluster engine using kubectl.

Every user gets their own namespace: lab-u<id>. The engine refuses commands
that try to change namespace or chain shell operations.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from shutil import which

from ..challenges import Challenge
from ..config import settings
from . import ApplyResult, Engine

TIMEOUT = 30
ALLOWED_VERBS = {
    "get", "describe", "logs", "set", "scale", "rollout",
    "delete", "create", "explain", "api-resources",
}
NAMESPACE_FLAGS = {"-n", "--namespace", "-A", "--all-namespaces"}
SHELL_TOKENS = (";", "|", "&", "`", "$(", ">", "<")


class KubernetesEngine(Engine):
    def __init__(self) -> None:
        if which("kubectl") is None:
            raise RuntimeError("LAB_MODE=kubernetes but the 'kubectl' binary is missing.")

    def _namespace(self, user_id: int) -> str:
        return f"{settings.namespace_prefix}{user_id}"

    def _run(self, args: list[str], stdin: str | None = None) -> ApplyResult:
        try:
            proc = subprocess.run(
                args, input=stdin, capture_output=True, text=True, timeout=TIMEOUT
            )
        except subprocess.TimeoutExpired:
            return ApplyResult(ok=False, output=f"timeout: {' '.join(args)}")
        output = (proc.stdout or "") + (proc.stderr or "")
        return ApplyResult(ok=proc.returncode == 0, output=output.strip())

    def _ensure_namespace(self, namespace: str) -> None:
        self._run(["kubectl", "create", "namespace", namespace])
        self._run([
            "kubectl", "label", "namespace", namespace,
            "app.kubernetes.io/part-of=k8s-lab", "--overwrite",
        ])

    def reset(self, user_id: int, challenge: Challenge) -> ApplyResult:
        namespace = self._namespace(user_id)
        self._ensure_namespace(namespace)

        if challenge.setup_yaml.strip():
            setup = self._run(
                ["kubectl", "apply", "-f", "-", "-n", namespace], stdin=challenge.setup_yaml
            )
            if not setup.ok:
                return setup

        applied = self._run(
            ["kubectl", "apply", "-f", "-", "-n", namespace], stdin=challenge.broken_yaml
        )
        if not applied.ok:
            return applied

        document = _parse(challenge.broken_yaml)
        return self.apply(user_id, challenge, document) if document else applied

    def apply(self, user_id: int, challenge: Challenge, document: dict) -> ApplyResult:
        import yaml

        namespace = self._namespace(user_id)
        payload = yaml.safe_dump(document, sort_keys=False)
        result = self._run(["kubectl", "apply", "-f", "-", "-n", namespace], stdin=payload)
        if not result.ok:
            return result
        status = self._run(["kubectl", "get", "pods", "-n", namespace, "-o", "wide"])
        return ApplyResult(ok=True, output=f"{result.output}\n\n{status.output}".strip())

    def current(self, user_id: int, challenge: Challenge) -> dict | None:
        namespace = self._namespace(user_id)
        resource = challenge.target_kind.lower()
        result = self._run([
            "kubectl", "get", resource, challenge.target_name,
            "-n", namespace, "-o", "json",
        ])
        if not result.ok:
            return None
        try:
            return json.loads(result.output)
        except json.JSONDecodeError:
            return None

    def terminal(self, user_id: int, challenge: Challenge, command: str) -> str:
        if any(token in command for token in SHELL_TOKENS):
            return "error: shell operators are not allowed in the lab terminal."
        try:
            tokens = shlex.split(command)
        except ValueError as exc:
            return f"error: cannot parse command ({exc})"

        if not tokens or tokens[0] != "kubectl":
            return "error: commands must start with 'kubectl'."
        if len(tokens) < 2 or tokens[1] not in ALLOWED_VERBS:
            return f"error: verb not allowed. Allowed: {', '.join(sorted(ALLOWED_VERBS))}."
        if any(token in NAMESPACE_FLAGS or token.startswith("--namespace=") for token in tokens):
            return "error: namespace flags are not allowed; the lab pins your namespace."

        args = tokens[:]
        if tokens[1] not in {"explain", "api-resources"}:
            args += ["-n", self._namespace(user_id)]

        result = self._run(args)
        return result.output or "(no output)"


def _parse(text: str) -> dict | None:
    import yaml

    document = yaml.safe_load(text)
    return document if isinstance(document, dict) else None
