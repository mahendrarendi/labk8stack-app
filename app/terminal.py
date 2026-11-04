"""Imperative command interpreter for the simulated sandbox.

Supports a useful subset of kubectl so learners can fix challenges either by
editing YAML or by typing commands. Both paths mutate the same Sandbox.
"""

from __future__ import annotations

import shlex

from .challenges import (
    GOOD_IMAGE_TAGS,
    NODE_CPU,
    NODE_MEMORY_MIB,
    Challenge,
    _image_tag,
    parse_cpu,
    parse_memory_mib,
)
from .sandbox import Sandbox

POD_IMAGE = "web-6d8f7c9b5d-abc12"
POD_PENDING = "web-7c5b9f8d6e-xyz34"
PODS_SERVICE = ["web-5f7c9d8b6-4k2lp", "web-5f7c9d8b6-9x7qz"]

HELP = """Available commands (simulated kubectl):
  kubectl get pods|deploy|deployments|svc|services|ep|endpoints
  kubectl describe pod <name>
  kubectl describe deployment <name>
  kubectl describe service <name>
  kubectl logs <pod> [--previous]
  kubectl set image deployment/<name> <container>=<image>
  kubectl scale deployment/<name> --replicas=<n>
  kubectl rollout status deployment/<name>
  kubectl delete pod <name>
  kubectl create deployment <name> --image=<image>
  help

The editor and this terminal share one sandbox: changes stack up."""


def _deployment(sandbox: Sandbox) -> dict | None:
    return sandbox.get("Deployment", "web")


def _service(sandbox: Sandbox) -> dict | None:
    return sandbox.get("Service", "web")


def _container(deployment: dict) -> dict:
    return deployment["spec"]["template"]["spec"]["containers"][0]


def _deployment_status(sandbox: Sandbox, challenge: Challenge) -> tuple[str, str]:
    """Return (READY, STATUS) for the challenge's primary pod."""
    deployment = _deployment(sandbox)
    if deployment is None:
        return "0/1", "NotFound"
    try:
        container = _container(deployment)
    except (KeyError, IndexError, TypeError):
        return "0/1", "Invalid"

    if challenge.slug == "pending-resources":
        requests = (container.get("resources") or {}).get("requests") or {}
        try:
            cpu = parse_cpu(requests.get("cpu", "0"))
            memory = parse_memory_mib(requests.get("memory", "0Mi"))
        except ValueError:
            return "0/1", "Pending"
        if cpu <= NODE_CPU and memory <= NODE_MEMORY_MIB:
            return "1/1", "Running"
        return "0/1", "Pending"

    image = str(container.get("image", ""))
    if _image_tag(image) in GOOD_IMAGE_TAGS:
        return "1/1", "Running"
    return "0/1", "ImagePullBackOff"


def _pod_rows(sandbox: Sandbox, challenge: Challenge) -> list[tuple[str, str, str]]:
    if challenge.slug == "service-no-endpoints":
        return [(name, "1/1", "Running") for name in PODS_SERVICE]
    ready, status = _deployment_status(sandbox, challenge)
    name = POD_PENDING if challenge.slug == "pending-resources" else POD_IMAGE
    return [(name, ready, status)]


def _endpoints(sandbox: Sandbox) -> str:
    service = _service(sandbox)
    if service is None:
        return "<none>"
    selector = (service.get("spec") or {}).get("selector") or {}
    if selector.get("app") == "web":
        return "10.244.0.15:8080,10.244.0.16:8080"
    return "<none>"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    line = "   ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    body = "\n".join(
        "   ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)) for row in rows
    )
    return line + "\n" + body if body else line


def _split_target(tokens: list[str]) -> tuple[str, str | None, list[str]]:
    """Accept both `deployment/web` and `deployment web` forms."""
    if not tokens:
        return "", None, []
    first = tokens[0]
    if "/" in first:
        kind, name = first.split("/", 1)
        return kind, name, tokens[1:]
    kind = first
    name = tokens[1] if len(tokens) > 1 and not tokens[1].startswith("-") else None
    rest = tokens[2:] if name else tokens[1:]
    return kind, name, rest


def _flag(rest: list[str], name: str) -> str | None:
    for index, token in enumerate(rest):
        if token == name and index + 1 < len(rest):
            return rest[index + 1]
        if token.startswith(f"{name}="):
            return token.split("=", 1)[1]
    return None


def execute(sandbox: Sandbox, challenge: Challenge, command: str) -> str:
    command = command.strip()
    if not command:
        return ""
    if command in {"help", "kubectl help", "?"}:
        return HELP

    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        return f"error: cannot parse command ({exc})"

    if tokens[0] != "kubectl":
        return f"error: unknown command '{tokens[0]}'. Type 'help' for the command list."
    if len(tokens) < 2:
        return HELP

    verb = tokens[1]
    rest = tokens[2:]

    if verb == "get":
        return _get(sandbox, challenge, rest)
    if verb == "describe":
        return _describe(sandbox, challenge, rest)
    if verb == "logs":
        return _logs(sandbox, challenge, rest)
    if verb == "set":
        if not rest or rest[0] != "image":
            return "error: usage: kubectl set image deployment/<name> <container>=<image>"
        return _set_image(sandbox, rest[1:])
    if verb == "scale":
        return _scale(sandbox, rest)
    if verb == "rollout":
        return _rollout(sandbox, challenge, rest)
    if verb == "delete":
        return _delete(rest)
    if verb == "create":
        return _create(sandbox, rest)
    if verb == "apply":
        return (
            "kubectl apply needs a file. Use the Manifest editor tab and click Apply, "
            "or change the resource with 'kubectl set image' / 'kubectl scale'."
        )
    return f"error: '{verb}' is not supported in this lab. Type 'help'."


def _get(sandbox: Sandbox, challenge: Challenge, rest: list[str]) -> str:
    if not rest:
        return "error: you must specify what to get (pods, deployments, services, endpoints)"
    what = rest[0]

    if what in {"pods", "pod", "po"}:
        rows = [[name, ready, status, "0", "4m"] for name, ready, status in _pod_rows(sandbox, challenge)]
        return _table(["NAME", "READY", "STATUS", "RESTARTS", "AGE"], rows)

    if what in {"deploy", "deployment", "deployments"}:
        deployment = _deployment(sandbox)
        if deployment is None:
            return "No resources found."
        ready = sum(1 for _, r, _ in _pod_rows(sandbox, challenge) if r == "1/1")
        total = len(_pod_rows(sandbox, challenge))
        return _table(
            ["NAME", "READY", "UP-TO-DATE", "AVAILABLE", "AGE"],
            [["web", f"{ready}/{total}", str(total), str(ready), "4m"]],
        )

    if what in {"svc", "service", "services"}:
        service = _service(sandbox)
        if service is None:
            return "No resources found."
        return _table(
            ["NAME", "TYPE", "CLUSTER-IP", "PORT(S)", "AGE"],
            [["web", "ClusterIP", "10.96.12.34", "80/TCP", "4m"]],
        )

    if what in {"ep", "endpoints", "endpoint"}:
        return _table(["NAME", "ENDPOINTS", "AGE"], [["web", _endpoints(sandbox), "4m"]])

    if what in {"all", "pods,svc", "pods,services"}:
        return "\n\n".join([
            _get(sandbox, challenge, ["pods"]),
            _get(sandbox, challenge, ["svc"]),
        ])

    return f"error: unknown resource type '{what}'"


def _describe(sandbox: Sandbox, challenge: Challenge, rest: list[str]) -> str:
    kind, name, _ = _split_target(rest)
    if not kind:
        return "error: you must specify a resource to describe"

    if kind in {"pod", "pods", "po"}:
        rows = _pod_rows(sandbox, challenge)
        selected = rows[0]
        if name:
            match = [row for row in rows if row[0] == name]
            if not match:
                return f'Error from server (NotFound): pods "{name}" not found'
            selected = match[0]
        pod_name, ready, status = selected
        deployment = _deployment(sandbox)
        image = ""
        if deployment is not None:
            try:
                image = str(_container(deployment).get("image", ""))
            except (KeyError, IndexError, TypeError):
                image = ""
        events = {
            "ImagePullBackOff": [
                'Warning  Failed  Failed to pull image "' + image + '": manifest unknown',
                "Warning  Failed  Error: ErrImagePull",
            ],
            "Pending": [
                "Warning  FailedScheduling  0/1 nodes are available: "
                "1 Insufficient cpu, 1 Insufficient memory.",
            ],
            "Running": ["Normal  Started  Container started"],
        }.get(status, [])
        lines = [
            f"Name:         {pod_name}",
            "Namespace:    lab",
            "Labels:       app=web",
            f"Status:       {status}",
            f"Ready:        {ready}",
            f"Image:        {image}",
            "",
            "Events:",
            *[f"  {event}" for event in events],
        ]
        return "\n".join(lines)

    if kind in {"deploy", "deployment", "deployments"}:
        deployment = _deployment(sandbox)
        if deployment is None:
            return f'Error from server (NotFound): deployments.apps "{name or "web"}" not found'
        try:
            container = _container(deployment)
        except (KeyError, IndexError, TypeError):
            return "Error: invalid Deployment"
        ready, status = _deployment_status(sandbox, challenge)
        return "\n".join([
            f"Name:               {name or 'web'}",
            "Namespace:          lab",
            f"Replicas:           {deployment.get('spec', {}).get('replicas', 1)} desired",
            f"Available:          {1 if ready == '1/1' else 0}",
            f"Container:          {container.get('name', 'web')}",
            f"  Image:            {container.get('image', '')}",
            f"  Requests:         {(container.get('resources') or {}).get('requests')}",
            f"Pod status:         {status}",
        ])

    if kind in {"svc", "service", "services"}:
        service = _service(sandbox)
        if service is None:
            return f'Error from server (NotFound): services "{name or "web"}" not found'
        return "\n".join([
            f"Name:              {name or 'web'}",
            "Namespace:         lab",
            "Type:              ClusterIP",
            "IP:                10.96.12.34",
            f"Selector:          {service.get('spec', {}).get('selector')}",
            f"Endpoints:         {_endpoints(sandbox)}",
            f"Port:              80/TCP",
        ])

    return f"error: describe for '{kind}' is not supported"


def _logs(sandbox: Sandbox, challenge: Challenge, rest: list[str]) -> str:
    if not rest:
        return "error: a pod name is required"
    _, status = _deployment_status(sandbox, challenge)
    if challenge.slug == "service-no-endpoints":
        return "\n".join([
            "[info] listening on 0.0.0.0:8080",
            '[info] GET /healthz 200',
            '[info] GET /api/info 200',
        ])
    if status == "ImagePullBackOff":
        return (
            f'Error from server (BadRequest): container "web" in pod "{rest[0]}" '
            "is waiting to start: trying and failing to pull image"
        )
    if status == "Pending":
        return f'Error from server (BadRequest): container "web" in pod "{rest[0]}" is not running'
    return "\n".join([
        "[info] listening on 0.0.0.0:8080",
        '[info] GET /healthz 200',
        '[info] GET /api/info 200',
    ])


def _set_image(sandbox: Sandbox, rest: list[str]) -> str:
    kind, name, tail = _split_target(rest)
    if kind not in {"deploy", "deployment", "deployments"}:
        return "error: usage: kubectl set image deployment/<name> <container>=<image>"
    assignment = tail[0] if tail else ""
    if "=" not in assignment:
        return "error: usage: kubectl set image deployment/<name> <container>=<image>"
    container_name, image = assignment.split("=", 1)

    deployment = _deployment(sandbox)
    if deployment is None:
        return f'Error from server (NotFound): deployments.apps "{name or "web"}" not found'
    try:
        container = _container(deployment)
    except (KeyError, IndexError, TypeError):
        return "Error: invalid Deployment"
    if container.get("name") and container.get("name") != container_name:
        return f'error: unable to find container named "{container_name}"'

    container["image"] = image
    sandbox.put(deployment)
    return f"deployment.apps/{name or 'web'} image updated"


def _scale(sandbox: Sandbox, rest: list[str]) -> str:
    kind, name, tail = _split_target(rest)
    if kind not in {"deploy", "deployment", "deployments"}:
        return "error: usage: kubectl scale deployment/<name> --replicas=<n>"
    value = _flag(tail, "--replicas")
    if value is None or not value.isdigit():
        return "error: --replicas must be a number"
    deployment = _deployment(sandbox)
    if deployment is None:
        return f'Error from server (NotFound): deployments.apps "{name or "web"}" not found'
    deployment.setdefault("spec", {})["replicas"] = int(value)
    sandbox.put(deployment)
    return f"deployment.apps/{name or 'web'} scaled"


def _rollout(sandbox: Sandbox, challenge: Challenge, rest: list[str]) -> str:
    if not rest or rest[0] != "status":
        return "error: usage: kubectl rollout status deployment/<name>"
    _, status = _deployment_status(sandbox, challenge)
    if status == "Running":
        return 'deployment "web" successfully rolled out'
    return f'Waiting for deployment "web" rollout to finish: current status {status}'


def _delete(rest: list[str]) -> str:
    kind, name, _ = _split_target(rest)
    if kind in {"pod", "pods", "po"}:
        return (
            f'pod "{name or "web"}" deleted. The Deployment controller will create '
            "a replacement pod with the same spec."
        )
    return f"error: deleting '{kind}' is not supported in this lab"


def _create(sandbox: Sandbox, rest: list[str]) -> str:
    kind, name, tail = _split_target(rest)
    if kind not in {"deploy", "deployment", "deployments"}:
        return "error: usage: kubectl create deployment <name> --image=<image>"
    image = _flag(tail, "--image")
    if not image:
        return "error: --image is required"
    name = name or "web"
    document = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "labels": {"app": name}},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": name}},
            "template": {
                "metadata": {"labels": {"app": name}},
                "spec": {"containers": [{"name": name, "image": image}]},
            },
        },
    }
    sandbox.put(document)
    return f"deployment.apps/{name} created"
