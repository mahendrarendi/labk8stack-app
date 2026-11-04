"""Simulator: tidak menyentuh cluster, cukup meniru output kubectl.

Berguna untuk demo, pengembangan UI, dan unit test. Perilakunya ditentukan
oleh challenge.simulate() berdasarkan isi YAML yang dikirim user.
"""

from __future__ import annotations

import time

from ..challenges import Challenge
from . import ApplyResult, Engine


class FakeEngine(Engine):
    def __init__(self) -> None:
        self._applied: dict[tuple[int, str], int] = {}

    def reset(self, user_id: int, challenge: Challenge) -> ApplyResult:
        self._applied[(user_id, challenge.slug)] = int(time.time())
        return ApplyResult(ok=True, output="sandbox direset ke kondisi rusak.")

    def apply(self, user_id: int, challenge: Challenge, document: dict) -> ApplyResult:
        lines = challenge.simulate(document)
        self._applied[(user_id, challenge.slug)] = int(time.time())
        return ApplyResult(ok=True, output="\n".join(lines))
