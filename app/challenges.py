"""Challenge catalog: broken manifests, objectives, validators, status views.

Every challenge exposes only the symptom. The cause (wrong selector, low
limit, bad probe, ...) is never written in ``summary``/``story``/``objective``
/``hint`` — diagnosing it is the exercise.

The ``status``/``pod_events``/``pod_logs`` callables are consumed by the
simulated terminal (``app/terminal.py``) so each challenge behaves like a
small cluster instead of a hardcoded scenario.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .config import settings

# "Node" capacity used by the simulator (and recommended in the lab cluster).
NODE_CPU = 2.0
NODE_MEMORY_MIB = 4096


def _split_image(image: str) -> tuple[str, str]:
    """Return (repository, tag) for an image reference."""
    last = image.rsplit("/", 1)[-1]
    if ":" not in last:
        return image, "latest"
    repository, tag = image.rsplit(":", 1)
    return repository, tag


APP_IMAGE = settings.app_image
APP_REPOSITORY, APP_TAG = _split_image(APP_IMAGE)
# The "broken" image keeps the same repository but an unpublished tag.
BAD_TAG_IMAGE = f"{APP_REPOSITORY}:0.0.3"
GOOD_IMAGE_TAGS = {
    tag.strip() for tag in settings.app_good_tags.split(",") if tag.strip()
} | {"0.1.0", "v0.1.0", "latest"}
MIN_MEMORY_MIB = 128
READY_PATH = "/healthz"
APP_PORT = 8080


def parse_cpu(value: object) -> float:
    text = str(value).strip()
    if text.endswith("m"):
        return float(text[:-1]) / 1000.0
    return float(text)


def parse_memory_mib(value: object) -> float:
    text = str(value).strip()
    units = {
        "Ki": 1 / 1024,
        "Mi": 1,
        "Gi": 1024,
        "Ti": 1024 * 1024,
        "K": 1000 / 1024**2,
        "M": 1000**2 / 1024**2,
        "G": 1000**3 / 1024**2,
    }
    for suffix, factor in units.items():
        if text.endswith(suffix):
            return float(text[: -len(suffix)]) * factor
    return float(text) / 1024**2  # assume bytes


def object_key(document: dict) -> tuple[str, str]:
    return (
        str(document.get("kind", "")),
        str((document.get("metadata") or {}).get("name", "")),
    )


def format_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))
    line = "   ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
    body = "\n".join(
        "   ".join(str(cell).ljust(widths[index]) for index, cell in enumerate(row))
        for row in rows
    )
    return line + "\n" + body if body else line


# --------------------------------------------------------------- helpers


def _container(document: dict) -> dict:
    return document["spec"]["template"]["spec"]["containers"][0]


def _template_labels(document: dict) -> dict:
    return (
        document.get("spec", {})
        .get("template", {})
        .get("metadata", {})
        .get("labels", {})
        or {}
    )


def _match_labels(document: dict) -> dict:
    return ((document.get("spec") or {}).get("selector") or {}).get("matchLabels") or {}


def _image_tag(image: str) -> str:
    last = image.rsplit("/", 1)[-1]
    return last.rsplit(":", 1)[-1] if ":" in last else "latest"


def _restarts(status: str) -> str:
    return "6" if status == "CrashLoopBackOff" else "0"


def _probe_path(container: dict) -> str | None:
    probe = container.get("readinessProbe") or {}
    return ((probe.get("httpGet") or {}).get("path"))


def _probe_port(container: dict) -> object:
    probe = container.get("readinessProbe") or {}
    return (probe.get("httpGet") or {}).get("port")


def _env_value(container: dict, name: str) -> str | None:
    for item in container.get("env") or []:
        if item.get("name") == name:
            return str(item.get("value"))
    return None


def _memory_limit_mib(container: dict) -> float | None:
    limit = ((container.get("resources") or {}).get("limits") or {}).get("memory")
    if limit is None:
        return None
    return parse_memory_mib(limit)


def _requests(container: dict) -> tuple[float, float]:
    requests = (container.get("resources") or {}).get("requests") or {}
    return (
        parse_cpu(requests.get("cpu", "0")),
        parse_memory_mib(requests.get("memory", "0Mi")),
    )


@dataclass(frozen=True)
class Challenge:
    slug: str
    title: str
    difficulty: str
    points: int
    duration_seconds: int
    summary: str
    story: str
    objective: str
    hint: str
    broken_yaml: str
    setup_yaml: str
    target_kind: str
    target_name: str
    validate: Callable[[dict], tuple[bool, str]]
    simulate: Callable[[dict], list[str]]
    # --- simulator metadata -------------------------------------------------
    pod_names: tuple[str, ...] = ("web-6d8f7c9b5d-abc12",)
    workload_kind: str = "Deployment"
    workload_name: str = "web"
    status: Callable[[dict], tuple[str, str]] | None = None
    pod_events: Callable[[dict], list[str]] | None = None
    pod_logs: Callable[[dict], list[str]] | None = None
    # Return False when the API server would reject the manifest outright.
    accepts: Callable[[dict], bool] | None = None


# ------------------------------------------------------ status functions


def status_image(document: dict) -> tuple[str, str]:
    try:
        image = str(_container(document).get("image", ""))
    except (KeyError, IndexError, TypeError):
        return "0/1", "Invalid"
    if _image_tag(image) in GOOD_IMAGE_TAGS:
        return "1/1", "Running"
    return "0/1", "ImagePullBackOff"


def status_service(document: dict) -> tuple[str, str]:
    return "1/1", "Running"


def status_pending(document: dict) -> tuple[str, str]:
    try:
        cpu, memory = _requests(_container(document))
    except (KeyError, IndexError, TypeError, ValueError):
        return "0/1", "Pending"
    if cpu <= NODE_CPU and memory <= NODE_MEMORY_MIB:
        return "1/1", "Running"
    return "0/1", "Pending"


def status_command(document: dict) -> tuple[str, str]:
    try:
        container = _container(document)
    except (KeyError, IndexError, TypeError):
        return "0/1", "Invalid"
    command = container.get("command")
    if command is None or list(command) == ["python3", "app.py"]:
        return "1/1", "Running"
    return "0/1", "CrashLoopBackOff"


def status_oom(document: dict) -> tuple[str, str]:
    try:
        limit = _memory_limit_mib(_container(document))
    except (KeyError, IndexError, TypeError, ValueError):
        return "1/1", "Running"
    if limit is None or limit >= MIN_MEMORY_MIB:
        return "1/1", "Running"
    return "0/1", "CrashLoopBackOff"


def status_readiness(document: dict) -> tuple[str, str]:
    try:
        container = _container(document)
    except (KeyError, IndexError, TypeError):
        return "0/1", "Invalid"
    path = _probe_path(container)
    port = _probe_port(container)
    if path == READY_PATH and str(port) == str(APP_PORT):
        return "1/1", "Running"
    return "0/1", "Running"


def status_port_env(document: dict) -> tuple[str, str]:
    try:
        container = _container(document)
    except (KeyError, IndexError, TypeError):
        return "0/1", "Invalid"
    port = _env_value(container, "PORT")
    if port is None or port == str(APP_PORT):
        return "1/1", "Running"
    return "0/1", "Running"


def status_target_port(document: dict) -> tuple[str, str]:
    return "1/1", "Running"


def status_selector(document: dict) -> tuple[str, str]:
    if _match_labels(document) != _template_labels(document):
        return "0/0", "Invalid"
    return "1/1", "Running"


def status_paused(document: dict) -> tuple[str, str]:
    if (document.get("spec") or {}).get("paused"):
        return "0/0", "Paused"
    return "1/1", "Running"


# -------------------------------------------------------- pod events/logs


def events_image(document: dict) -> list[str]:
    try:
        image = str(_container(document).get("image", ""))
    except (KeyError, IndexError, TypeError):
        image = ""
    return [
        f'Warning  Failed  Failed to pull image "{image}": manifest unknown',
        "Warning  Failed  Error: ErrImagePull",
        "Warning  BackOff  Back-off pulling image",
    ]


def events_pending(document: dict) -> list[str]:
    return [
        "Warning  FailedScheduling  0/1 nodes are available: "
        "1 Insufficient cpu, 1 Insufficient memory.",
    ]


def events_command(document: dict) -> list[str]:
    return [
        "Normal   Started    Started container web",
        "Warning  BackOff    Back-off restarting failed container",
        "Warning  Failed     Error: container exited with code 2",
    ]


def events_oom(document: dict) -> list[str]:
    return [
        "Warning  OOMKilled  Container web was OOMKilled (exit code 137)",
        "Warning  BackOff    Back-off restarting failed container",
    ]


def events_readiness(document: dict) -> list[str]:
    try:
        container = _container(document)
    except (KeyError, IndexError, TypeError):
        return []
    path = _probe_path(container)
    if path == READY_PATH and str(_probe_port(container)) == str(APP_PORT):
        return ["Normal  Started  Container started"]
    return [
        "Warning  Unhealthy  Readiness probe failed: "
        "HTTP probe failed with statuscode: 404",
    ]


def events_port_env(document: dict) -> list[str]:
    try:
        container = _container(document)
    except (KeyError, IndexError, TypeError):
        return []
    if _env_value(container, "PORT") in (None, str(APP_PORT)):
        return ["Normal  Started  Container started"]
    return [
        "Warning  Unhealthy  Readiness probe failed: dial tcp :8080: "
        "connect: connection refused",
    ]


def events_normal(document: dict) -> list[str]:
    return ["Normal  Started  Container started"]


def logs_normal(document: dict) -> list[str]:
    return [
        "[info] listening on 0.0.0.0:8080",
        "[info] GET /healthz 200",
        "[info] GET /api/info 200",
    ]


def logs_command(document: dict) -> list[str]:
    return [
        "python3: can't open file '/app/server.py': [Errno 2] "
        "No such file or directory",
    ]


def logs_oom(document: dict) -> list[str]:
    return [
        "[info] loading configuration...",
        "[info] allocating 200Mi for the in-memory cache...",
    ]


def logs_port_env(document: dict) -> list[str]:
    return [
        "[info] listening on 0.0.0.0:9090",
        '[info] GET /healthz 200',
    ]


# -------------------------------------------------------------- simulation


def make_simulate(
    status: Callable[[dict], tuple[str, str]],
    pod_names: tuple[str, ...],
    events: Callable[[dict], list[str]] | None = None,
    resource: str = "deployment.apps/web",
) -> Callable[[dict], list[str]]:
    def simulate(document: dict) -> list[str]:
        ready, state = status(document)
        if state == "Invalid":
            return [
                'The Deployment "web" is invalid: '
                "spec.template.metadata.labels: Invalid value: "
                "`selector` does not match template `labels`",
            ]
        if state == "Paused":
            return [f"{resource} created", f"{resource} paused"]
        rows = [
            [name, ready, state, _restarts(state), "4s"] for name in pod_names
        ]
        lines = [
            f"{resource} created",
            "",
            format_table(["NAME", "READY", "STATUS", "RESTARTS", "AGE"], rows),
        ]
        extra = events(document) if events else []
        if extra:
            lines += ["", *extra]
        return lines

    return simulate


# ---------------------------------------------------------------- #1 image

IMAGE_BROKEN = f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  labels:
    app: web
spec:
  replicas: 1
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
    spec:
      containers:
        - name: web
          image: {BAD_TAG_IMAGE}
          ports:
            - containerPort: {APP_PORT}
"""


def validate_image(document: dict) -> tuple[bool, str]:
    if document.get("kind") != "Deployment":
        return False, "The resource must be a Deployment."
    try:
        image = str(_container(document).get("image", ""))
    except (KeyError, IndexError, TypeError):
        return False, "Invalid Deployment: no containers found."
    if _image_tag(image) in GOOD_IMAGE_TAGS:
        return True, f"The pod is ready and running with image '{image}'."
    return False, (
        "The Deployment is still not healthy: its pod never becomes ready. "
        "Inspect the pod and its events."
    )


# -------------------------------------------------------------- #2 service

SERVICE_BROKEN = """apiVersion: v1
kind: Service
metadata:
  name: web
spec:
  type: ClusterIP
  selector:
    app: web-v2
  ports:
    - port: 80
      targetPort: 8080
      protocol: TCP
"""

SERVICE_SETUP = f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  labels:
    app: web
spec:
  replicas: 2
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
    spec:
      containers:
        - name: web
          image: {APP_IMAGE}
          ports:
            - containerPort: {APP_PORT}
"""


def validate_service_selector(document: dict) -> tuple[bool, str]:
    if document.get("kind") != "Service":
        return False, "The resource must be a Service."
    selector = (document.get("spec") or {}).get("selector") or {}
    if selector.get("app") == "web":
        return True, "The Service now reaches the application pods behind it."
    return False, (
        "Requests through the Service would still fail: no pod is receiving "
        "traffic. Compare the Service with the pods and check the endpoints."
    )


def simulate_service_selector(document: dict) -> list[str]:
    selector = (document.get("spec") or {}).get("selector") or {}
    if selector.get("app") == "web":
        return [
            "service/web created",
            "",
            format_table(
                ["NAME", "ENDPOINTS", "AGE"],
                [["web", "10.244.0.15:8080,10.244.0.16:8080", "2s"]],
            ),
        ]
    return [
        "service/web created",
        "",
        format_table(["NAME", "ENDPOINTS", "AGE"], [["web", "<none>", "2s"]]),
    ]


# -------------------------------------------------------------- #3 pending

PENDING_BROKEN = f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  labels:
    app: web
spec:
  replicas: 1
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
    spec:
      containers:
        - name: web
          image: {APP_IMAGE}
          ports:
            - containerPort: {APP_PORT}
          resources:
            requests:
              cpu: "8"
              memory: "16Gi"
            limits:
              cpu: "8"
              memory: "16Gi"
"""


def validate_pending(document: dict) -> tuple[bool, str]:
    if document.get("kind") != "Deployment":
        return False, "The resource must be a Deployment."
    try:
        cpu, memory = _requests(_container(document))
    except (KeyError, IndexError, TypeError, ValueError):
        return False, "resources.requests is missing or malformed."
    if cpu <= NODE_CPU and memory <= NODE_MEMORY_MIB:
        return True, "The pod is scheduled and running on a node."
    return False, (
        "The pod is still Pending and has not been scheduled. Check the pod "
        "events and the node's available capacity."
    )


# -------------------------------------------------------------- #4 command

COMMAND_BROKEN = f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  labels:
    app: web
spec:
  replicas: 1
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
    spec:
      containers:
        - name: web
          image: {APP_IMAGE}
          command:
            - python3
            - server.py
          ports:
            - containerPort: {APP_PORT}
"""


def validate_command(document: dict) -> tuple[bool, str]:
    if document.get("kind") != "Deployment":
        return False, "The resource must be a Deployment."
    try:
        container = _container(document)
    except (KeyError, IndexError, TypeError):
        return False, "Invalid Deployment: no containers found."
    command = container.get("command")
    if command is None or list(command) == ["python3", "app.py"]:
        return True, "The container starts with the application entrypoint and stays running."
    return False, (
        "The container is still restarting in a loop. Check its previous "
        "output and compare the startup command with what the image expects."
    )


# ------------------------------------------------------------------ #5 oom

OOM_BROKEN = f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  labels:
    app: web
spec:
  replicas: 1
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
    spec:
      containers:
        - name: web
          image: {APP_IMAGE}
          ports:
            - containerPort: {APP_PORT}
          resources:
            requests:
              cpu: 100m
              memory: 32Mi
            limits:
              cpu: 500m
              memory: 32Mi
"""


def validate_oom(document: dict) -> tuple[bool, str]:
    if document.get("kind") != "Deployment":
        return False, "The resource must be a Deployment."
    try:
        container = _container(document)
        limit = _memory_limit_mib(container)
        _, requested = _requests(container)
    except (KeyError, IndexError, TypeError, ValueError):
        return False, "resources.limits is malformed."
    if limit is not None and limit < MIN_MEMORY_MIB:
        return False, (
            "The container is still being killed while starting. Give it more "
            "room and make sure the request does not exceed the limit."
        )
    if limit is not None and requested > limit:
        return False, "The request is larger than the limit; the pod cannot run."
    return True, "The container starts and stays up without being killed."


# ------------------------------------------------------------ #6 readiness

READINESS_BROKEN = f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  labels:
    app: web
spec:
  replicas: 1
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
    spec:
      containers:
        - name: web
          image: {APP_IMAGE}
          ports:
            - containerPort: {APP_PORT}
          readinessProbe:
            httpGet:
              path: /health
              port: {APP_PORT}
            initialDelaySeconds: 2
            periodSeconds: 5
"""


def validate_readiness(document: dict) -> tuple[bool, str]:
    if document.get("kind") != "Deployment":
        return False, "The resource must be a Deployment."
    try:
        container = _container(document)
    except (KeyError, IndexError, TypeError):
        return False, "Invalid Deployment: no containers found."
    if _probe_path(container) == READY_PATH and str(_probe_port(container)) == str(APP_PORT):
        return True, "Every pod reports Ready and receives traffic."
    return False, (
        "The pods still never become Ready, so the Service has no usable "
        "endpoints. Inspect the readiness probe and the pod events."
    )


# ------------------------------------------------------------- #7 port env

PORT_ENV_BROKEN = f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  labels:
    app: web
spec:
  replicas: 1
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
    spec:
      containers:
        - name: web
          image: {APP_IMAGE}
          ports:
            - containerPort: {APP_PORT}
          env:
            - name: PORT
              value: "9090"
          readinessProbe:
            httpGet:
              path: {READY_PATH}
              port: {APP_PORT}
            initialDelaySeconds: 2
            periodSeconds: 5
"""


def validate_port_env(document: dict) -> tuple[bool, str]:
    if document.get("kind") != "Deployment":
        return False, "The resource must be a Deployment."
    try:
        container = _container(document)
    except (KeyError, IndexError, TypeError):
        return False, "Invalid Deployment: no containers found."
    if _env_value(container, "PORT") in (None, str(APP_PORT)):
        return True, "The container listens where the probes and Service expect it."
    return False, (
        "The container is running but never becomes Ready. Compare the port "
        "it logs at startup with the port used by the probe."
    )


# -------------------------------------------------------- #8 target port

TARGET_PORT_BROKEN = f"""apiVersion: v1
kind: Service
metadata:
  name: web
spec:
  type: ClusterIP
  selector:
    app: web
  ports:
    - port: 80
      targetPort: 8081
      protocol: TCP
"""

TARGET_PORT_SETUP = f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  labels:
    app: web
spec:
  replicas: 2
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
    spec:
      containers:
        - name: web
          image: {APP_IMAGE}
          ports:
            - containerPort: {APP_PORT}
"""


def validate_target_port(document: dict) -> tuple[bool, str]:
    if document.get("kind") != "Service":
        return False, "The resource must be a Service."
    try:
        target = (document.get("spec") or {}).get("ports")[0].get("targetPort")
    except (KeyError, IndexError, TypeError):
        return False, "The Service has no ports configured."
    if str(target) == str(APP_PORT):
        return True, "The Service forwards traffic to the port the app listens on."
    return False, (
        "Clients still cannot reach the application through the Service. "
        "Compare the Service port mapping with the port the container exposes."
    )


def simulate_target_port(document: dict) -> list[str]:
    try:
        target = (document.get("spec") or {}).get("ports")[0].get("targetPort")
    except (KeyError, IndexError, TypeError):
        target = "?"
    if str(target) == str(APP_PORT):
        return [
            "service/web created",
            "",
            format_table(
                ["NAME", "ENDPOINTS", "AGE"],
                [["web", "10.244.0.15:8080,10.244.0.16:8080", "2s"]],
            ),
        ]
    return [
        "service/web created",
        "",
        format_table(
            ["NAME", "ENDPOINTS", "AGE"],
            [["web", f"10.244.0.15:{target},10.244.0.16:{target}", "2s"]],
        ),
    ]


# ----------------------------------------------------------- #9 selector

SELECTOR_BROKEN = f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  labels:
    app: web
spec:
  replicas: 1
  selector:
    matchLabels:
      app: web-api
  template:
    metadata:
      labels:
        app: web
    spec:
      containers:
        - name: web
          image: {APP_IMAGE}
          ports:
            - containerPort: {APP_PORT}
"""


def validate_selector(document: dict) -> tuple[bool, str]:
    if document.get("kind") != "Deployment":
        return False, "The resource must be a Deployment."
    if _match_labels(document) == _template_labels(document):
        return True, "The Deployment is valid and its pod is running."
    return False, (
        "The API server would still reject this Deployment: no pod can be "
        "created. Read the validation error and compare the selector with the "
        "pod template labels."
    )


def accepts_selector(document: dict) -> bool:
    return _match_labels(document) == _template_labels(document)


# ------------------------------------------------------------ #10 paused

PAUSED_BROKEN = f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  labels:
    app: web
spec:
  replicas: 1
  paused: true
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
    spec:
      containers:
        - name: web
          image: {APP_IMAGE}
          ports:
            - containerPort: {APP_PORT}
"""


def validate_paused(document: dict) -> tuple[bool, str]:
    if document.get("kind") != "Deployment":
        return False, "The resource must be a Deployment."
    if (document.get("spec") or {}).get("paused"):
        return False, (
            "The rollout is still paused, so no pod is created. Inspect the "
            "Deployment state and resume it."
        )
    return True, "The rollout is running and the pod is available."


# ------------------------------------------------------------------ catalog


CHALLENGES: dict[str, Challenge] = {
    "image-pull-backoff": Challenge(
        slug="image-pull-backoff",
        title="Deployment never becomes ready",
        difficulty="Easy",
        points=100,
        duration_seconds=600,
        summary=(
            "A Deployment was rolled out a few minutes ago. The pod never "
            "reaches a ready state."
        ),
        story=(
            "Your team shipped a new Deployment and handed you the manifests. "
            "Since the rollout, the application has been unreachable and the "
            "pod is not ready. Nobody has changed anything else in the cluster."
        ),
        objective=(
            "Get Deployment 'web' into a healthy state: its pod must become "
            "ready and running."
        ),
        hint=(
            "Start with 'kubectl get pods', then 'kubectl describe pod <name>'. "
            "Read the Events section at the bottom of the description."
        ),
        broken_yaml=IMAGE_BROKEN,
        setup_yaml="",
        target_kind="Deployment",
        target_name="web",
        validate=validate_image,
        simulate=make_simulate(
            status_image, ("web-6d8f7c9b5d-abc12",), events_image
        ),
        status=status_image,
        pod_events=events_image,
    ),
    "service-no-endpoints": Challenge(
        slug="service-no-endpoints",
        title="Service returns 503",
        difficulty="Easy",
        points=110,
        duration_seconds=600,
        summary=(
            "Every request through the Service returns 503, even though the "
            "application pods appear to be up."
        ),
        story=(
            "The application is deployed and the pods look healthy, but "
            "clients calling the Service always get a 503. The application "
            "logs show no errors and no incoming requests."
        ),
        objective=(
            "Make the Service actually serve traffic to the running "
            "application."
        ),
        hint=(
            "Compare what the Service expects with what the pods actually "
            "provide: 'kubectl get pods --show-labels', "
            "'kubectl describe service web', and 'kubectl get endpoints'."
        ),
        broken_yaml=SERVICE_BROKEN,
        setup_yaml=SERVICE_SETUP,
        target_kind="Service",
        target_name="web",
        validate=validate_service_selector,
        simulate=simulate_service_selector,
        pod_names=("web-5f7c9d8b6-4k2lp", "web-5f7c9d8b6-9x7qz"),
        status=status_service,
        pod_events=events_normal,
        pod_logs=logs_normal,
    ),
    "pending-resources": Challenge(
        slug="pending-resources",
        title="Pod never schedules",
        difficulty="Medium",
        points=140,
        duration_seconds=600,
        summary=(
            "A pod stays Pending forever: it exists, but it never runs on a node."
        ),
        story=(
            "A Deployment was applied and its pod was created, but the pod "
            "never leaves Pending. The application itself has never started, "
            "so there are no logs to inspect."
        ),
        objective=(
            "Get the pod scheduled onto a node and running with the "
            "application available."
        ),
        hint=(
            "'kubectl describe pod <name>' shows the scheduler's reasoning in "
            "the Events section, including the capacity it compared against."
        ),
        broken_yaml=PENDING_BROKEN,
        setup_yaml="",
        target_kind="Deployment",
        target_name="web",
        validate=validate_pending,
        simulate=make_simulate(
            status_pending, ("web-7c5b9f8d6e-xyz34",), events_pending
        ),
        pod_names=("web-7c5b9f8d6e-xyz34",),
        status=status_pending,
        pod_events=events_pending,
    ),
    "command-override": Challenge(
        slug="command-override",
        title="Container restarts in a loop",
        difficulty="Easy",
        points=110,
        duration_seconds=600,
        summary=(
            "The pod is created but keeps restarting. It never stays ready "
            "for long."
        ),
        story=(
            "A teammate added a custom startup command while debugging and "
            "shipped it. Since then the pod restarts every few seconds and the "
            "application is never available."
        ),
        objective=(
            "Make the container start successfully and stay running with the "
            "application's normal entrypoint."
        ),
        hint=(
            "'kubectl get pods' shows a growing restart count. "
            "'kubectl logs <pod> --previous' shows the output of the crashed "
            "process."
        ),
        broken_yaml=COMMAND_BROKEN,
        setup_yaml="",
        target_kind="Deployment",
        target_name="web",
        validate=validate_command,
        simulate=make_simulate(
            status_command, ("web-84d9c6f7b1-k2m4n",), events_command
        ),
        pod_names=("web-84d9c6f7b1-k2m4n",),
        status=status_command,
        pod_events=events_command,
        pod_logs=logs_command,
    ),
    "oom-killed": Challenge(
        slug="oom-killed",
        title="Container is killed while starting",
        difficulty="Medium",
        points=140,
        duration_seconds=600,
        summary=(
            "The pod starts, then dies, then starts again. The application "
            "never finishes booting."
        ),
        story=(
            "After a resource tuning change the application cannot stay up. "
            "The restarts keep climbing and the container never serves "
            "requests."
        ),
        objective=(
            "Give the container enough memory to run and keep it schedulable "
            "on the node."
        ),
        hint=(
            "'kubectl describe pod <name>' shows how the previous run ended. "
            "Compare the container resources with what the application needs "
            "while it boots."
        ),
        broken_yaml=OOM_BROKEN,
        setup_yaml="",
        target_kind="Deployment",
        target_name="web",
        validate=validate_oom,
        simulate=make_simulate(
            status_oom, ("web-59f7d8c6a3-p8q2r",), events_oom
        ),
        pod_names=("web-59f7d8c6a3-p8q2r",),
        status=status_oom,
        pod_events=events_oom,
        pod_logs=logs_oom,
    ),
    "readiness-probe": Challenge(
        slug="readiness-probe",
        title="Pods never become Ready",
        difficulty="Medium",
        points=130,
        duration_seconds=600,
        summary=(
            "The pods are running, but the Deployment reports 0 ready "
            "replicas and every request fails."
        ),
        story=(
            "The application is up and its logs look healthy, but clients "
            "still cannot reach it. The Deployment never reports a ready "
            "replica."
        ),
        objective=(
            "Make every pod report Ready so the Service can route traffic to "
            "it."
        ),
        hint=(
            "'kubectl describe pod <name>' lists probe failures under Events. "
            "Compare the probe with the routes the application actually serves: "
            "'kubectl logs <pod>'."
        ),
        broken_yaml=READINESS_BROKEN,
        setup_yaml="",
        target_kind="Deployment",
        target_name="web",
        validate=validate_readiness,
        simulate=make_simulate(
            status_readiness, ("web-6b8c9d7f42-t5v6w",), events_readiness
        ),
        pod_names=("web-6b8c9d7f42-t5v6w",),
        status=status_readiness,
        pod_events=events_readiness,
        pod_logs=logs_normal,
    ),
    "port-env": Challenge(
        slug="port-env",
        title="Application unreachable after config change",
        difficulty="Medium",
        points=130,
        duration_seconds=600,
        summary=(
            "The container runs without crashing, but the pod never becomes "
            "Ready and receives no traffic."
        ),
        story=(
            "A configuration value was changed during a deploy. The container "
            "is up and logging normally, yet the pod never reports Ready."
        ),
        objective=(
            "Get the application to listen on the port the cluster expects, "
            "and the pod to become Ready."
        ),
        hint=(
            "Read 'kubectl logs <pod>' carefully and compare the port in the "
            "startup line with the probe and container port."
        ),
        broken_yaml=PORT_ENV_BROKEN,
        setup_yaml="",
        target_kind="Deployment",
        target_name="web",
        validate=validate_port_env,
        simulate=make_simulate(
            status_port_env, ("web-7d9e8f6a21-c3d4e",), events_port_env
        ),
        pod_names=("web-7d9e8f6a21-c3d4e",),
        status=status_port_env,
        pod_events=events_port_env,
        pod_logs=logs_port_env,
    ),
    "service-target-port": Challenge(
        slug="service-target-port",
        title="Service connections are refused",
        difficulty="Medium",
        points=130,
        duration_seconds=600,
        summary=(
            "The pods are Ready and the Service has endpoints, but clients "
            "still cannot connect."
        ),
        story=(
            "The Deployment is healthy and the Service is registered. Every "
            "request to it still fails at the connection level."
        ),
        objective=(
            "Make requests through the Service reach the application."
        ),
        hint=(
            "'kubectl get endpoints' shows where traffic is sent. "
            "'kubectl describe service web' shows the port mapping; compare it "
            "with the port the container exposes."
        ),
        broken_yaml=TARGET_PORT_BROKEN,
        setup_yaml=TARGET_PORT_SETUP,
        target_kind="Service",
        target_name="web",
        validate=validate_target_port,
        simulate=simulate_target_port,
        pod_names=("web-4a6b7c8d9e-f1g2h", "web-4a6b7c8d9e-h3j4k"),
        status=status_target_port,
        pod_events=events_normal,
        pod_logs=logs_normal,
    ),
    "deployment-selector": Challenge(
        slug="deployment-selector",
        title="Deployment cannot be applied",
        difficulty="Hard",
        points=150,
        duration_seconds=600,
        summary=(
            "The rollout produced no pods at all, and applying the manifest "
            "returns an error."
        ),
        story=(
            "A new manifest was prepared for the release, but nothing was "
            "created. The apply command reports that the Deployment is "
            "invalid."
        ),
        objective=(
            "Make the Deployment valid so the controller can create its pods."
        ),
        hint=(
            "Read the apply error closely. Compare the Deployment selector with "
            "the labels on its pod template: 'kubectl get deployment web -o "
            "yaml'."
        ),
        broken_yaml=SELECTOR_BROKEN,
        setup_yaml="",
        target_kind="Deployment",
        target_name="web",
        validate=validate_selector,
        simulate=make_simulate(
            status_selector, ("web-9c8b7a6d5e-zz11",)
        ),
        pod_names=("web-9c8b7a6d5e-zz11",),
        status=status_selector,
        accepts=accepts_selector,
    ),
    "deployment-paused": Challenge(
        slug="deployment-paused",
        title="Rollout never progresses",
        difficulty="Hard",
        points=150,
        duration_seconds=600,
        summary=(
            "The Deployment exists but has no pods, and waiting does not "
            "change anything."
        ),
        story=(
            "The release was applied, but no pod was ever created. Watching "
            "the rollout forever shows no progress."
        ),
        objective=(
            "Get the rollout to create and run its pod."
        ),
        hint=(
            "'kubectl describe deployment web' prints the rollout state. If "
            "the rollout is held, resume it."
        ),
        broken_yaml=PAUSED_BROKEN,
        setup_yaml="",
        target_kind="Deployment",
        target_name="web",
        validate=validate_paused,
        simulate=make_simulate(
            status_paused, ("web-2e4f6a8b0c-nm55",)
        ),
        pod_names=("web-2e4f6a8b0c-nm55",),
        status=status_paused,
    ),
}


def get_challenge(slug: str) -> Challenge | None:
    return CHALLENGES.get(slug)
