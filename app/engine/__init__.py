"""Engine interface shared by the simulator and the real cluster."""

from __future__ import annotations

from dataclasses import dataclass

from ..challenges import Challenge
from ..config import settings


@dataclass
class ApplyResult:
    ok: bool
    output: str


class Engine:
    """Both modes must implement these four operations."""

    def reset(self, user_id: int, challenge: Challenge) -> ApplyResult:
        raise NotImplementedError

    def apply(self, user_id: int, challenge: Challenge, document: dict) -> ApplyResult:
        raise NotImplementedError

    def current(self, user_id: int, challenge: Challenge) -> dict | None:
        raise NotImplementedError

    def terminal(self, user_id: int, challenge: Challenge, command: str) -> str:
        raise NotImplementedError

    def check(self, user_id: int, challenge: Challenge) -> tuple[bool, str]:
        """Check live workload health after the challenge-specific spec check."""
        raise NotImplementedError

    def release(self, user_id: int, challenge: Challenge) -> ApplyResult:
        """Remove resources after a completed attempt."""
        raise NotImplementedError


def get_engine() -> Engine:
    if settings.mode == "kubernetes":
        from .kubernetes import KubernetesEngine

        return KubernetesEngine()
    from .fake import FakeEngine

    return FakeEngine()
