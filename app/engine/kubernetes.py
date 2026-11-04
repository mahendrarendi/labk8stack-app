"""Engine Kubernetes asli memakai `kubectl` (tanpa dependensi Python k8s).

Setiap user mendapat namespace sendiri: lab-u<id>. Resource challenge yang
rusak dibuat di namespace itu, sehingga user hanya bisa merusak sandbox-nya.

Prasyarat di image/container platform:
  - binary `kubectl`
  - kubeconfig dengan izin membuat namespace & resource di namespace tersebut
"""

from __future__ import annotations

import subprocess
from shutil import which

from ..challenges import Challenge
from ..config import settings
from . import ApplyResult, Engine

TIMEOUT = 30


class KubernetesEngine(Engine):
    def __init__(self) -> None:
        if which("kubectl") is None:
            raise RuntimeError(
                "LAB_MODE=kubernetes tetapi binary 'kubectl' tidak ditemukan."
            )

    def _namespace(self, user_id: int) -> str:
        return f"{settings.namespace_prefix}{user_id}"

    def _run(self, args: list[str], stdin: str | None = None) -> ApplyResult:
        try:
            proc = subprocess.run(
                args,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
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
                ["kubectl", "apply", "-f", "-", "-n", namespace],
                stdin=challenge.setup_yaml,
            )
            if not setup.ok:
                return setup

        applied = self._run(
            ["kubectl", "apply", "-f", "-", "-n", namespace],
            stdin=challenge.broken_yaml,
        )
        if not applied.ok:
            return applied
        return self.apply(user_id, challenge, _parse(challenge.broken_yaml))

    def apply(self, user_id: int, challenge: Challenge, document: dict) -> ApplyResult:
        import yaml

        namespace = self._namespace(user_id)
        payload = yaml.safe_dump(document, sort_keys=False)
        result = self._run(
            ["kubectl", "apply", "-f", "-", "-n", namespace], stdin=payload
        )
        if not result.ok:
            return result

        status = self._run(
            ["kubectl", "get", "pods", "-n", namespace, "-o", "wide"]
        )
        return ApplyResult(ok=True, output=f"{result.output}\n\n{status.output}".strip())


def _parse(text: str) -> dict:
    import yaml

    return yaml.safe_load(text)
