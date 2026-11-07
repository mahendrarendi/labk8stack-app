"""Real-cluster engine using kubectl.

Every user/challenge gets an isolated namespace: ``lab-u<id>-<slug>``. Inside it the platform
provisions a ServiceAccount (``lab-runner``) bound to the ``lab-user``
ClusterRole, and runs every user-facing command with ``--as`` so the API server
enforces the namespace-scoped permissions instead of trusting the platform.

Required environment:
    LAB_MODE=kubernetes
    LAB_KUBECONFIG=/path/to/kubeconfig   # or run in-cluster

Impersonation is mandatory in Kubernetes mode.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from datetime import datetime, timedelta, timezone
from shutil import which

from ..challenges import APP_PORT, Challenge
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

# ServiceAccount + RoleBinding created inside every lab namespace. Using
# `kubectl apply` keeps it idempotent, so a retry no longer depends on the
# namespace being recreated.
_PROVISIONING = """apiVersion: v1
kind: ServiceAccount
metadata:
  name: {service_account}
  namespace: {namespace}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: lab-runner
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: {cluster_role}
subjects:
  - kind: ServiceAccount
    name: {service_account}
    namespace: {namespace}
---
apiVersion: v1
kind: ResourceQuota
metadata:
  name: lab-quota
spec:
  hard:
    pods: "12"
    requests.cpu: "16"
    requests.memory: 32Gi
    persistentvolumeclaims: "2"
    services: "4"
---
apiVersion: v1
kind: LimitRange
metadata:
  name: lab-limits
spec:
  limits:
    - type: Container
      defaultRequest:
        cpu: 50m
        memory: 64Mi
      default:
        cpu: "10"
        memory: 20Gi
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: lab-isolation
spec:
  podSelector: {{}}
  policyTypes: [Ingress, Egress]
  ingress:
    - from:
        - podSelector: {{}}
  egress:
    - to:
        - podSelector: {{}}
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
"""


class KubernetesEngine(Engine):
    def __init__(self) -> None:
        if which("kubectl") is None:
            raise RuntimeError("LAB_MODE=kubernetes but the 'kubectl' binary is missing.")
        if not settings.impersonate:
            raise RuntimeError("LAB_MODE=kubernetes requires LAB_IMPERSONATE=true.")

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

    def _namespace_phase(self, namespace: str) -> str | None:
        result = self._run(["kubectl", "get", "namespace", namespace, "-o", "json"])
        if not result.ok:
            return None
        try:
            document = json.loads(result.output)
        except json.JSONDecodeError:
            return None
        return (document.get("status") or {}).get("phase")

    def _ensure_namespace(self, namespace: str, duration_seconds: int) -> ApplyResult | None:
        """Create the namespace and its runner identity if they are missing."""
        phase = self._namespace_phase(namespace)
        if phase == "Terminating":
            # A deleted namespace cannot be reused. Wait for the controller to
            # finish instead of applying into a dying namespace.
            for _ in range(60):
                time.sleep(2)
                if self._namespace_phase(namespace) is None:
                    break
            else:
                return ApplyResult(
                    ok=False,
                    output=(
                        f"namespace {namespace} is still terminating; "
                        "wait a moment and reload the challenge."
                    ),
                )
            phase = None

        if phase is None:
            created = self._run(["kubectl", "create", "namespace", namespace])
            if not created.ok and "AlreadyExists" not in created.output:
                return created

        labeled = self._run([
            "kubectl", "label", "namespace", namespace,
            "app.kubernetes.io/part-of=k8s-lab",
            "k8s-lab.io/component=sandbox", "--overwrite",
        ])
        if not labeled.ok:
            return labeled
        secured = self._run([
            "kubectl", "label", "namespace", namespace,
            "pod-security.kubernetes.io/enforce=baseline", "--overwrite",
        ])
        if not secured.ok:
            return secured
        expiry = datetime.now(timezone.utc) + timedelta(
            seconds=duration_seconds,
            minutes=settings.sandbox_grace_minutes,
        )
        annotated = self._run([
            "kubectl", "annotate", "namespace", namespace,
            f"k8s-lab.io/expires-at={expiry.isoformat(timespec='seconds')}",
            "--overwrite",
        ])
        if not annotated.ok:
            return annotated
        if not settings.impersonate:
            return None

        provisioning = _PROVISIONING.format(
            service_account=settings.runner_service_account,
            cluster_role=settings.runner_cluster_role,
            namespace=namespace,
        )
        applied = self._run(
            ["kubectl", "apply", "-f", "-", "-n", namespace],
            stdin=provisioning,
            timeout=30,
        )
        return None if applied.ok else applied

    def _clean(self, namespace: str) -> ApplyResult:
        """Remove challenge resources but keep the namespace and its identity.

        Deleting and recreating the namespace is slow and, on some clusters,
        leaves it stuck in Terminating. Wiping the objects inside it has the
        same effect for the learner and is much more predictable.
        """
        deleted = self._run(
            [
                "kubectl", "delete",
                "deployments,services",
                "--all", "-n", namespace, "--ignore-not-found",
                "--wait=true", "--timeout=30s",
            ],
            impersonate=True,
            namespace=namespace,
            timeout=45,
        )
        if not deleted.ok:
            return deleted
        # Pods removed by their controller can still be terminating.
        deleted = self._run(
            [
                "kubectl", "delete", "pods", "--all",
                "-n", namespace, "--ignore-not-found", "--wait=false",
            ],
            impersonate=True,
            namespace=namespace,
            timeout=30,
        )
        return deleted

    # ------------------------------------------------------------- engine

    def reset(self, user_id: int, challenge: Challenge) -> ApplyResult:
        namespace = self._namespace(user_id, challenge)
        provisioned = self._ensure_namespace(namespace, challenge.duration_seconds)
        if provisioned is not None:
            return provisioned

        cleaned = self._clean(namespace)
        if not cleaned.ok:
            return cleaned

        if challenge.setup_yaml.strip():
            setup = self._run(
                ["kubectl", "apply", "-f", "-", "-n", namespace],
                stdin=challenge.setup_yaml,
                impersonate=True,
                namespace=namespace,
            )
            if not setup.ok:
                return setup

        result = self._run(
            ["kubectl", "apply", "-f", "-", "-n", namespace],
            stdin=challenge.broken_yaml,
            impersonate=True,
            namespace=namespace,
        )
        if not result.ok and challenge.accepts is not None:
            import yaml
            if not challenge.accepts(yaml.safe_load(challenge.broken_yaml)):
                return ApplyResult(ok=True, output=result.output)
        return result

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

        # The editor is the only path that accepts arbitrary manifests. Keep
        # terminal mutations to the imperative operations used by this lab.
        if any(t in {"-f", "--filename", "-k", "--kustomize", "--raw"}
               or t.startswith(("--filename=", "--kustomize=", "--raw="))
               for t in tokens):
            return "error: file, URL, and raw API input are disabled. Use the editor."
        if tokens[1] == "create" and (len(tokens) < 3 or tokens[2] != "deployment"):
            return "error: only 'kubectl create deployment' is allowed."
        if tokens[1] == "delete" and (len(tokens) < 3 or tokens[2] not in {"pod", "pods"}):
            return "error: only 'kubectl delete pod' is allowed."
        if tokens[1] == "set" and (len(tokens) < 3 or tokens[2] not in {"image", "env", "resources"}):
            return "error: that 'kubectl set' operation is not allowed."
        if tokens[1] == "rollout" and (len(tokens) < 3 or tokens[2] not in {"status", "resume", "pause"}):
            return "error: that rollout operation is not allowed."

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

    def check(self, user_id: int, challenge: Challenge) -> tuple[bool, str]:
        namespace = self._namespace(user_id, challenge)
        if challenge.target_kind == "Service":
            result = self._run(
                ["kubectl", "get", "endpoints", challenge.target_name,
                 "-n", namespace, "-o", "json"],
                impersonate=True, namespace=namespace,
            )
            if not result.ok:
                return False, result.output
            try:
                subsets = json.loads(result.output).get("subsets") or []
                ready = any(s.get("addresses") and any(
                    p.get("port") == APP_PORT for p in s.get("ports") or []
                ) for s in subsets)
            except (ValueError, AttributeError, TypeError):
                ready = False
            return (True, f"Service has ready endpoints on port {APP_PORT}.") if ready else (
                False, f"Service has no ready endpoint on port {APP_PORT} yet."
            )

        result = self._run(
            ["kubectl", "rollout", "status", f"deployment/{challenge.workload_name}", "-n", namespace,
             "--timeout=15s"],
            impersonate=True, namespace=namespace, timeout=20,
        )
        return result.ok, result.output or "Deployment has not become ready yet."

    def release(self, user_id: int, challenge: Challenge) -> ApplyResult:
        namespace = self._namespace(user_id, challenge)
        return self._run(
            ["kubectl", "delete", "namespace", namespace, "--wait=false",
             "--ignore-not-found"], timeout=30,
        )
