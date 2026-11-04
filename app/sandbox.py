"""In-memory sandbox state shared by the editor and the terminal.

One sandbox per (user, challenge). Changes made through the YAML editor and
through imperative terminal commands land in the same objects, so a learner can
mix both styles freely.
"""

from __future__ import annotations

import copy
import yaml

from .challenges import Challenge, object_key


class Sandbox:
    def __init__(self, challenge: Challenge) -> None:
        self.challenge = challenge
        self.objects: dict[tuple[str, str], dict] = {}
        self.load(challenge.setup_yaml)
        self.load(challenge.broken_yaml)

    def load(self, text: str) -> None:
        if not text.strip():
            return
        for document in yaml.safe_load_all(text):
            if isinstance(document, dict):
                self.put(document)

    def put(self, document: dict) -> None:
        self.objects[object_key(document)] = copy.deepcopy(document)

    def get(self, kind: str, name: str | None = None) -> dict | None:
        if name is not None:
            return self.objects.get((kind, name))
        for (object_kind, _), document in self.objects.items():
            if object_kind == kind:
                return document
        return None

    def delete(self, kind: str, name: str) -> dict | None:
        return self.objects.pop((kind, name), None)


_REGISTRY: dict[tuple[int, str], Sandbox] = {}


def get_sandbox(user_id: int, challenge: Challenge) -> Sandbox:
    key = (user_id, challenge.slug)
    if key not in _REGISTRY:
        _REGISTRY[key] = Sandbox(challenge)
    return _REGISTRY[key]


def reset_sandbox(user_id: int, challenge: Challenge) -> Sandbox:
    key = (user_id, challenge.slug)
    _REGISTRY[key] = Sandbox(challenge)
    return _REGISTRY[key]
