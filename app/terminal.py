"""Imperative command interpreter for the simulated sandbox.

Supports a useful subset of kubectl so learners can fix challenges either by
editing YAML or by typing commands. Both paths mutate the same Sandbox.

Per-challenge behaviour (pod status, events, logs) comes from ``Challenge``
callables in ``app/challenges.py``; this module only renders it.
"""

from __future__ import annotations

import shlex

from .challenges import APP_PORT, Challenge, format_table
from .sandbox import Sandbox

HELP = """Available commands (simulated kubectl):
  kubectl get pods|deploy|deployments|svc|services|ep|endpoints
  kubectl describe pod <name>
  kubectl describe deployment <name>
  kubectl describe service <name>
  kubectl logs <pod> [--previous]
  kubectl set image deployment/<name> <container>=<image>
  kubectl set env deployment/<name> <KEY>=<value>
  kubectl set resources deployment/<name> --limits=cpu=500m,memory=256Mi
  kubectl scale deployment/<name> --replicas=<n>
  kubectl rollout status deployment/<name>
  kubectl rollout resume deployment/<name>
  kubectl rollout pause deployment/<name>
  kubectl delete pod <name>
  kubectl create deployment <name> --image=<image>
  help

The editor and this terminal share one sandbox: changes stack up."""

NO_PODS = "No resources found in lab namespace."


# ------------------------------------------------------------- accessors


def _workload(sandbox: Sandbox, challenge: Challenge) -> dict | None:
    return sandbox.get(challenge.workload_kind, challenge.workload_name)


def _service(sandbox: Sandbox) -> dict | None:
    return sandbox.get("Service", "web")


def _container(workload: dict) -> dict:
    return workload["spec"]["template"]["spec"]["containers"][0]


def _template_labels(workload: dict) -> dict:
    return (
        workload.get("spec", {})
        .get("template", {})
        .get("metadata", {})
        .get("labels", {})
        or {}
    )


def _status(workload: dict, challenge: Challenge) -> tuple[str, str]:
    if challenge.status is None:
        return "1/1", "Running"
    try:
        return challenge.status(workload)
    except (KeyError, IndexError, TypeError, ValueError):
        return "0/1", "Invalid"


def _pod_rows(sandbox: Sandbox, challenge: Challenge) -> list[tuple[str, str, str]]:
    workload = _workload(sandbox, challenge)
    if workload is None:
        return []
    ready, state = _status(workload, challenge)
    if state in {"Invalid", "Paused"}:
        return []
    return [(name, ready, state) for name in challenge.pod_names]


def _pod_events(workload: dict, challenge: Challenge) -> list[str]:
    if challenge.pod_events is None:
        return []
    try:
        return challenge.pod_events(workload)
    except (KeyError, IndexError, TypeError, ValueError):
        return []


def _pod_logs(workload: dict, challenge: Challenge) -> list[str]:
    if challenge.pod_logs is None:
        return [
            "[info] listening on 0.0.0.0:8080",
            "[info] GET /healthz 200",
            "[info] GET /api/info 200",
        ]
    try:
        return challenge.pod_logs(workload)
    except (KeyError, IndexError, TypeError, ValueError):
        return []


def _endpoints(sandbox: Sandbox) -> tuple[str, str]:
    """Return (endpoints, target_port) for the Service."""
    service = _service(sandbox)
    if service is None:
        return "<none>", str(APP_PORT)
    spec = service.get("spec") or {}
    selector = spec.get("selector") or {}
    try:
        target = str(spec.get("ports")[0].get("targetPort", APP_PORT))
    except (KeyError, IndexError, TypeError):
        target = str(APP_PORT)
    if selector.get("app") != "web":
        return "<none>", target
    addresses = [f"10.244.0.15:{target}", f"10.244.0.16:{target}"]
    return ",".join(addresses), target


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


def _restarts(state: str) -> str:
    return "6" if state == "CrashLoopBackOff" else "0"


# --------------------------------------------------------------- dispatch


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
        if not rest:
            return "error: usage: kubectl set image|env deployment/<name> ..."
        if rest[0] == "image":
            return _set_image(sandbox, challenge, rest[1:])
        if rest[0] == "env":
            return _set_env(sandbox, challenge, rest[1:])
        if rest[0] == "resources":
            return _set_resources(sandbox, challenge, rest[1:])
        return "error: usage: kubectl set image|env|resources deployment/<name> ..."
    if verb == "scale":
        return _scale(sandbox, challenge, rest)
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
        rows = [
            [name, ready, status, _restarts(status), "4m"]
            for name, ready, status in _pod_rows(sandbox, challenge)
        ]
        if not rows:
            return NO_PODS
        return format_table(["NAME", "READY", "STATUS", "RESTARTS", "AGE"], rows)

    if what in {"deploy", "deployment", "deployments"}:
        workload = _workload(sandbox, challenge)
        if workload is None:
            return NO_PODS
        pods = _pod_rows(sandbox, challenge)
        ready = sum(1 for _, r, _ in pods if r == "1/1")
        desired = int((workload.get("spec") or {}).get("replicas", len(pods)) or 0)
        return format_table(
            ["NAME", "READY", "UP-TO-DATE", "AVAILABLE", "AGE"],
            [["web", f"{ready}/{desired}", str(len(pods)), str(ready), "4m"]],
        )

    if what in {"svc", "service", "services"}:
        service = _service(sandbox)
        if service is None:
            return NO_PODS
        try:
            port = (service.get("spec") or {}).get("ports")[0]
            ports = f"{port.get('port', 80)}/TCP"
        except (KeyError, IndexError, TypeError):
            ports = "80/TCP"
        return format_table(
            ["NAME", "TYPE", "CLUSTER-IP", "PORT(S)", "AGE"],
            [["web", "ClusterIP", "10.96.12.34", ports, "4m"]],
        )

    if what in {"ep", "endpoints", "endpoint"}:
        endpoints, _ = _endpoints(sandbox)
        return format_table(["NAME", "ENDPOINTS", "AGE"], [["web", endpoints, "4m"]])

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
        workload = _workload(sandbox, challenge)
        if not rows or workload is None:
            wanted = name or (challenge.pod_names[0] if challenge.pod_names else "web")
            return f'Error from server (NotFound): pods "{wanted}" not found'
        selected = rows[0]
        if name:
            match = [row for row in rows if row[0] == name]
            if not match:
                return f'Error from server (NotFound): pods "{name}" not found'
            selected = match[0]
        pod_name, ready, status = selected
        try:
            container = _container(workload)
            image = str(container.get("image", ""))
            ports = ",".join(
                str(port.get("containerPort"))
                for port in container.get("ports") or []
                if port.get("containerPort") is not None
            )
        except (KeyError, IndexError, TypeError):
            image, ports = "", ""
        labels = ",".join(f"{k}={v}" for k, v in _template_labels(workload).items())
        lines = [
            f"Name:         {pod_name}",
            "Namespace:    lab",
            f"Labels:       {labels}",
            f"Status:       {status}",
            f"Ready:        {ready}",
            f"Restarts:     {_restarts(status)}",
            f"Image:        {image}",
            f"Ports:        {ports}",
            "",
            "Events:",
        ]
        events = _pod_events(workload, challenge)
        lines += [f"  {event}" for event in events] if events else ["  <none>"]
        return "\n".join(lines)

    if kind in {"deploy", "deployment", "deployments"}:
        workload = _workload(sandbox, challenge)
        if workload is None:
            return f'Error from server (NotFound): deployments.apps "{name or "web"}" not found'
        try:
            container = _container(workload)
        except (KeyError, IndexError, TypeError):
            return "Error: invalid Deployment"
        ready, status = _status(workload, challenge)
        spec = workload.get("spec") or {}
        template_labels = ",".join(
            f"{k}={v}" for k, v in _template_labels(workload).items()
        )
        selector = ",".join(
            f"{k}={v}" for k, v in (spec.get("selector") or {}).get("matchLabels", {}).items()
        )
        return "\n".join([
            f"Name:               {name or 'web'}",
            "Namespace:          lab",
            f"Replicas:           {spec.get('replicas', 1)} desired",
            f"Available:          {1 if ready == '1/1' else 0}",
            f"Paused:             {bool(spec.get('paused'))}",
            f"Selector:           {selector}",
            f"Template Labels:    {template_labels}",
            f"Container:          {container.get('name', 'web')}",
            f"  Image:            {container.get('image', '')}",
            f"  Requests:         {(container.get('resources') or {}).get('requests')}",
            f"Pod status:         {status}",
        ])

    if kind in {"svc", "service", "services"}:
        service = _service(sandbox)
        if service is None:
            return f'Error from server (NotFound): services "{name or "web"}" not found'
        endpoints, target = _endpoints(sandbox)
        spec = service.get("spec") or {}
        try:
            port = spec.get("ports")[0]
            mapping = f"{port.get('port', 80)}/{port.get('protocol', 'TCP')} -> {port.get('targetPort')}"
        except (KeyError, IndexError, TypeError):
            mapping = "?"
        selector = ",".join(f"{k}={v}" for k, v in (spec.get("selector") or {}).items())
        return "\n".join([
            f"Name:              {name or 'web'}",
            "Namespace:         lab",
            "Type:              ClusterIP",
            "IP:                10.96.12.34",
            f"Selector:          {selector}",
            f"Endpoints:         {endpoints}",
            f"Port:              {mapping}",
        ])

    return f"error: describe for '{kind}' is not supported"


def _logs(sandbox: Sandbox, challenge: Challenge, rest: list[str]) -> str:
    if not rest:
        return "error: a pod name is required"
    workload = _workload(sandbox, challenge)
    if workload is None:
        return f'Error from server (NotFound): pods "{rest[0]}" not found'
    _, status = _status(workload, challenge)
    if status in {"Invalid", "Paused"}:
        return f'Error from server (NotFound): pods "{rest[0]}" not found'
    if status in {"ImagePullBackOff", "Pending"}:
        return (
            f'Error from server (BadRequest): container "web" in pod "{rest[0]}" '
            "is waiting to start: container is not running"
        )
    return "\n".join(_pod_logs(workload, challenge))


def _set_image(sandbox: Sandbox, challenge: Challenge, rest: list[str]) -> str:
    kind, name, tail = _split_target(rest)
    if kind not in {"deploy", "deployment", "deployments"}:
        return "error: usage: kubectl set image deployment/<name> <container>=<image>"
    assignment = tail[0] if tail else ""
    if "=" not in assignment:
        return "error: usage: kubectl set image deployment/<name> <container>=<image>"
    container_name, image = assignment.split("=", 1)

    workload = _workload(sandbox, challenge)
    if workload is None:
        return f'Error from server (NotFound): deployments.apps "{name or "web"}" not found'
    try:
        container = _container(workload)
    except (KeyError, IndexError, TypeError):
        return "Error: invalid Deployment"
    if container.get("name") and container.get("name") != container_name:
        return f'error: unable to find container named "{container_name}"'

    container["image"] = image
    sandbox.put(workload)
    return f"deployment.apps/{name or 'web'} image updated"


def _set_env(sandbox: Sandbox, challenge: Challenge, rest: list[str]) -> str:
    kind, name, tail = _split_target(rest)
    if kind not in {"deploy", "deployment", "deployments"}:
        return "error: usage: kubectl set env deployment/<name> <KEY>=<value>"
    assignment = tail[0] if tail else ""
    if "=" not in assignment:
        return "error: usage: kubectl set env deployment/<name> <KEY>=<value>"
    key, value = assignment.split("=", 1)

    workload = _workload(sandbox, challenge)
    if workload is None:
        return f'Error from server (NotFound): deployments.apps "{name or "web"}" not found'
    try:
        container = _container(workload)
    except (KeyError, IndexError, TypeError):
        return "Error: invalid Deployment"

    env = container.setdefault("env", [])
    for item in env:
        if item.get("name") == key:
            item["value"] = value
            break
    else:
        env.append({"name": key, "value": value})
    sandbox.put(workload)
    return f"deployment.apps/{name or 'web'} env updated"


def _set_resources(sandbox: Sandbox, challenge: Challenge, rest: list[str]) -> str:
    kind, name, tail = _split_target(rest)
    if kind not in {"deploy", "deployment", "deployments"}:
        return "error: usage: kubectl set resources deployment/<name> --limits=memory=256Mi"
    requests = _flag(tail, "--requests")
    limits = _flag(tail, "--limits")
    if requests is None and limits is None:
        return "error: usage: kubectl set resources deployment/<name> --limits=memory=256Mi"

    workload = _workload(sandbox, challenge)
    if workload is None:
        return f'Error from server (NotFound): deployments.apps "{name or "web"}" not found'
    try:
        container = _container(workload)
    except (KeyError, IndexError, TypeError):
        return "Error: invalid Deployment"

    resources = container.setdefault("resources", {})
    for section, spec in (("requests", requests), ("limits", limits)):
        if spec is None:
            continue
        bucket = resources.setdefault(section, {})
        for item in spec.split(","):
            if "=" not in item:
                return f"error: cannot parse '{item}' in --{section}"
            key, value = item.split("=", 1)
            bucket[key.strip()] = value.strip()
    sandbox.put(workload)
    return f"deployment.apps/{name or 'web'} resource requirements updated"


def _scale(sandbox: Sandbox, challenge: Challenge, rest: list[str]) -> str:
    kind, name, tail = _split_target(rest)
    if kind not in {"deploy", "deployment", "deployments"}:
        return "error: usage: kubectl scale deployment/<name> --replicas=<n>"
    value = _flag(tail, "--replicas")
    if value is None or not value.isdigit():
        return "error: --replicas must be a number"
    workload = _workload(sandbox, challenge)
    if workload is None:
        return f'Error from server (NotFound): deployments.apps "{name or "web"}" not found'
    workload.setdefault("spec", {})["replicas"] = int(value)
    sandbox.put(workload)
    return f"deployment.apps/{name or 'web'} scaled"


def _rollout(sandbox: Sandbox, challenge: Challenge, rest: list[str]) -> str:
    if not rest or rest[0] not in {"status", "resume", "pause"}:
        return "error: usage: kubectl rollout status|resume|pause deployment/<name>"
    action = rest[0]
    workload = _workload(sandbox, challenge)
    if workload is None:
        return 'Error from server (NotFound): deployments.apps "web" not found'

    if action == "resume":
        workload.setdefault("spec", {})["paused"] = False
        sandbox.put(workload)
        return 'deployment.apps/web resumed'
    if action == "pause":
        workload.setdefault("spec", {})["paused"] = True
        sandbox.put(workload)
        return 'deployment.apps/web paused'

    if (workload.get("spec") or {}).get("paused"):
        return (
            'Waiting for deployment "web" rollout to finish: '
            "0 of 1 updated replicas are available..."
        )
    _, status = _status(workload, challenge)
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
