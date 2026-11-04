"""Real-cluster engine using kubectl.

Every user gets an isolated namespace: ``lab-u<id>``. Inside it the platform
provisions a ServiceAccount (``lab-runner``) bound to the ``lab-user``
ClusterRole, and runs every user-facing command with ``--as`` so the API server
enforces the namespace-scoped permissions instead of trusting the platform.

Required environment:
    LAB_MODE=kubernetes
    LAB_KUBECONFIG=/path/to/kubeconfig   # or run in-cluster

Optional:
    LAB_IMPERSONATE=false   # quick/dev mode: use the kubeconfig identity
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from shutil import which

from ..challenges import Challenge
from ..config import settings
from . import ApplyResult, Engine

TIMEOUT = 60
ALLOWED_VERBS = {
    "get", "describe", "logs", "set", "scale", "rollout",
    "delete", "create", "explain", "api-resources",
}
NAMESPACE_FLAGS = {"-n", "--namespace", "-A", "--all-namespaces"}
# Flags that would let a learner talk to another cluster/identity.
IDENTITY_FLAGS = {
    "-s", "--server", "--kubeconfig", "--context", "--cluster", "--user",
    "--token", "--as", "--as-group", "--certificate-authority",
    "--client-certificate", "--client-key", "--insecure-skip-tls-verify",
}
SHELL_TOKENS = (";", "|", "&", "`", "$(", ">", "<")


class KubernetesEngine(Engine):
    def __init__(self) -> None:
        if which("kubectl") is None:
            raise RuntimeError("LAB_MODE=kubernetes but the 'kubectl' binary is missing.")

    # ------------------------------------------------------------ helpers

    def _namespace(self, user_id: int, challenge: Challenge) -> str:
        return f"{settings.namespace_prefix}{user_id}-{challenge.slug}"

    def _runner(self, namespace: str) -> str:
        return f"system:serviceaccount:{namespace}:{settings.runner_service_account}"

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        if settings.kubeconfig:
            env["KUBECONFIG"] = settings.kubeconfig
        return env

    def _run(
        self,
        args: list[str],
        stdin: str | None = None,
        *,
        impersonate: bool = False,
        namespace: str | None = None,
        timeout: int = TIMEOUT,
    ) -> ApplyResult:
        command = list(args)
        if impersonate and settings.impersonate and namespace:
            command.append(f"--as={self._runner(namespace)}")
        try:
            proc = subprocess.run(
                command,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=self._env(),
            )
        except subprocess.TimeoutExpired:
            return ApplyResult(ok=False, output=f"timeout: {' '.join(command)}")
        output = (proc.stdout or "") + (proc.stderr or "")
        return ApplyResult(ok=proc.returncode == 0, output=output.strip())

    def _ensure_namespace(self, namespace: str) -> None:
        self._run(["kubectl", "create", "namespace", namespace])
        self._run([
            "kubectl", "label", "namespace", namespace,
            "app.kubernetes.io/part-of=k8s-lab",
            "k8s-lab.io/component=sandbox", "--overwrite",
        ])
        if not settings.impersonate:
            return
        self._run([
            "kubectl", "create", "serviceaccount",
            settings.runner_service_account, "-n", namespace,
        ])
        self._run([
            "kubectl", "create", "rolebinding", "lab-runner",
            f"--clusterrole={settings.runner_cluster_role}",
            f"--serviceaccount={namespace}:{settings.runner_service_account}",
            "-n", namespace,
        ])

    def _wipe_namespace(self, namespace: str) -> None:
        """Delete and recreate so every attempt starts from a clean slate."""
        self._run(
            [
                "kubectl", "delete", "namespace", namespace,
                "--ignore-not-found", "--wait=true", "--timeout=45s",
            ],
            timeout=60,
        )

    # ------------------------------------------------------------- engine

    def reset(self, user_id: int, challenge: Challenge) -> ApplyResult:
        namespace = self._namespace(user_id, challenge)
        self._wipe_namespace(namespace)
        self._ensure_namespace(namespace)

        if challenge.setup_yaml.strip():
            setup = self._run(
                ["kubectl", "apply", "-f", "-", "-n", namespace],
                stdin=challenge.setup_yaml,
                impersonate=True,
                namespace=namespace,
            )
            if not setup.ok:
                return setup

        applied = self._run(
            ["kubectl", "apply", "-f", "-", "-n", namespace],
            stdin=challenge.broken_yaml,
            impersonate=True,
            namespace=namespace,
        )
        if not applied.ok:
            return applied

        document = _parse(challenge.broken_yaml)
        return self.apply(user_id, challenge, document) if document else applied

    def apply(self, user_id: int, challenge: Challenge, document: dict) -> ApplyResult:
        import yaml

        namespace = self._namespace(user_id, challenge)
        payload = yaml.safe_dump(document, sort_keys=False)
        result = self._run(
            ["kubectl", "apply", "-f", "-", "-n", namespace],
            stdin=payload,
            impersonate=True,
            namespace=namespace,
        )
        if not result.ok:
            return result
        status = self._run(
            ["kubectl", "get", "pods", "-n", namespace, "-o", "wide"],
            impersonate=True,
            namespace=namespace,
        )
        return ApplyResult(ok=True, output=f"{result.output}\n\n{status.output}".strip())

    def current(self, user_id: int, challenge: Challenge) -> dict | None:
        namespace = self._namespace(user_id, challenge)
        resource = challenge.target_kind.lower()
        result = self._run(
            [
                "kubectl", "get", resource, challenge.target_name,
                "-n", namespace, "-o", "json",
            ],
            impersonate=True,
            namespace=namespace,
        )
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

        forbidden = NAMESPACE_FLAGS | IDENTITY_FLAGS
        for token in tokens:
            if token in forbidden or any(
                token.startswith(f"{flag}=") for flag in forbidden
            ):
                return (
                    "error: that flag is not allowed; the lab pins your namespace "
                    "and identity."
                )

        namespace = self._namespace(user_id, challenge)
        args = tokens[:]
        if tokens[1] not in {"explain", "api-resources"}:
            args += ["-n", namespace]

        result = self._run(
            args, impersonate=True, namespace=namespace
        )
        return result.output or "(no output)"


def _parse(text: str) -> dict | None:
    import yaml

    document = yaml.safe_load(text)
    return document if isinstance(document, dict) else None
