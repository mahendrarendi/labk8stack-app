"""Simulator engine: no cluster required.

State lives in app.sandbox, shared with the terminal interpreter, so the YAML
editor and imperative commands operate on the same objects.
"""

from __future__ import annotations

from ..challenges import Challenge
from ..sandbox import get_sandbox, reset_sandbox
from ..terminal import execute
from . import ApplyResult, Engine


class FakeEngine(Engine):
    def reset(self, user_id: int, challenge: Challenge) -> ApplyResult:
        reset_sandbox(user_id, challenge)
        return ApplyResult(ok=True, output="Sandbox reset to the broken state.")

    def apply(self, user_id: int, challenge: Challenge, document: dict) -> ApplyResult:
        sandbox = get_sandbox(user_id, challenge)
        sandbox.put(document)
        return ApplyResult(ok=True, output="\n".join(challenge.simulate(document)))

    def current(self, user_id: int, challenge: Challenge) -> dict | None:
        sandbox = get_sandbox(user_id, challenge)
        return sandbox.get(challenge.target_kind, challenge.target_name)

    def terminal(self, user_id: int, challenge: Challenge, command: str) -> str:
        sandbox = get_sandbox(user_id, challenge)
        return execute(sandbox, challenge, command)
