"""Engine abstrak: simulator dan implementasi Kubernetes asli."""

from __future__ import annotations

from dataclasses import dataclass

from ..challenges import Challenge
from ..config import settings


@dataclass
class ApplyResult:
    ok: bool
    output: str


class Engine:
    """Interface yang harus dipenuhi kedua mode."""

    def reset(self, user_id: int, challenge: Challenge) -> ApplyResult:
        raise NotImplementedError

    def apply(self, user_id: int, challenge: Challenge, document: dict) -> ApplyResult:
        raise NotImplementedError


def get_engine() -> Engine:
    if settings.mode == "kubernetes":
        from .kubernetes import KubernetesEngine

        return KubernetesEngine()
    from .fake import FakeEngine

    return FakeEngine()
