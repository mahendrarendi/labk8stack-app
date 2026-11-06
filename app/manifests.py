"""Safety checks for manifests submitted by learners.

In ``LAB_MODE=kubernetes`` the user's manifest is applied to a real namespace.
RBAC limits *where* it can go, but a namespaced Pod with ``hostPath`` or
``privileged`` could still escape to the node. Pod Security Admission may not be
enforced, so the platform refuses those fields itself.
"""

from __future__ import annotations

ALLOWED_KINDS = {
    "Deployment",
    "Service",
}

# Keys that are never acceptable in a lab manifest.
FORBIDDEN_KEYS = {
    "hostPath",
    "hostNetwork",
    "hostPID",
    "hostIPC",
    "hostPort",
    "privileged",
    "nodeName",
}

# Capabilities that would defeat the container sandbox.
FORBIDDEN_CAPABILITIES = {
    "SYS_ADMIN",
    "SYS_PTRACE",
    "SYS_MODULE",
    "NET_ADMIN",
    "NET_RAW",
    "DAC_READ_SEARCH",
    "DAC_OVERRIDE",
}


def safety_error(document: dict) -> str | None:
    kind = str(document.get("kind", ""))
    if kind not in ALLOWED_KINDS:
        allowed = ", ".join(sorted(ALLOWED_KINDS))
        return f"kind '{kind}' is not allowed in this lab. Allowed: {allowed}."

    problem = _scan(document)
    if problem:
        return (
            f"manifest rejected: '{problem}' is not allowed in the lab "
            "sandbox (node access is blocked)."
        )
    return None


def _scan(node: object) -> str | None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in FORBIDDEN_KEYS:
                return key
            if key == "capabilities":
                found = _capability(value)
                if found:
                    return f"capability {found}"
            nested = _scan(value)
            if nested:
                return nested
    elif isinstance(node, list):
        for item in node:
            nested = _scan(item)
            if nested:
                return nested
    return None


def _capability(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    added = value.get("add") or []
    if not isinstance(added, list):
        return None
    for capability in added:
        if str(capability).upper() in FORBIDDEN_CAPABILITIES:
            return str(capability)
    return None
