"""Challenge catalog: broken manifests, objectives, validators, status views."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

# "Node" capacity used by the simulator (and recommended in the lab cluster).
NODE_CPU = 2.0
NODE_MEMORY_MIB = 4096


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


# ------------------------------------------------------------------ #1

IMAGE_BROKEN = """apiVersion: apps/v1
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
          image: ghcr.io/change-me-owner/k8s-lab-app:0.0.3
          ports:
            - containerPort: 8080
"""

GOOD_IMAGE_TAGS = {"0.1.0", "v0.1.0", "latest"}


def _image_tag(image: str) -> str:
    last = image.rsplit("/", 1)[-1]
    return last.rsplit(":", 1)[-1] if ":" in last else "latest"


def validate_image(doc: dict) -> tuple[bool, str]:
    if doc.get("kind") != "Deployment":
        return False, "The resource must be a Deployment."
    try:
        container = doc["spec"]["template"]["spec"]["containers"][0]
    except (KeyError, IndexError, TypeError):
        return False, "Invalid Deployment: no containers found."
    image = str(container.get("image", ""))
    if _image_tag(image) in GOOD_IMAGE_TAGS:
        return True, f"The pod is ready and running with image '{image}'."
    return False, (
        "The Deployment is still not healthy: its pod never becomes ready. "
        "Inspect the pod and its events."
    )


def simulate_image(doc: dict) -> list[str]:
    try:
        image = str(doc["spec"]["template"]["spec"]["containers"][0]["image"])
    except (KeyError, IndexError, TypeError):
        return ["error: unable to parse Deployment"]
    tag = _image_tag(image)
    if tag in GOOD_IMAGE_TAGS:
        return [
            "deployment.apps/web created",
            "",
            "NAME                   READY   STATUS    RESTARTS   AGE",
            "web-6d8f7c9b5d-abc12   1/1     Running   0          3s",
        ]
    return [
        "deployment.apps/web created",
        "",
        "NAME                   READY   STATUS             RESTARTS   AGE",
        "web-6d8f7c9b5d-abc12   0/1     ImagePullBackOff   0          4s",
        "",
        f'Warning  Failed  Failed to pull image "{image}": manifest unknown',
        "Warning  Failed  Error: ErrImagePull",
    ]


# ------------------------------------------------------------------ #2

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

SERVICE_SETUP = """apiVersion: apps/v1
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
          image: ghcr.io/change-me-owner/k8s-lab-app:0.1.0
          ports:
            - containerPort: 8080
"""


def validate_service(doc: dict) -> tuple[bool, str]:
    if doc.get("kind") != "Service":
        return False, "The resource must be a Service."
    selector = (doc.get("spec") or {}).get("selector") or {}
    if selector.get("app") == "web":
        return True, "The Service now reaches the application pods behind it."
    return False, (
        "Requests through the Service would still fail: no pod is receiving "
        "traffic. Compare the Service with the pods and check the endpoints."
    )


def simulate_service(doc: dict) -> list[str]:
    selector = (doc.get("spec") or {}).get("selector") or {}
    if selector.get("app") == "web":
        return [
            "service/web created",
            "",
            "NAME   ENDPOINTS                               AGE",
            "web    10.244.0.15:8080,10.244.0.16:8080      2s",
        ]
    return [
        "service/web created",
        "",
        "NAME   ENDPOINTS   AGE",
        "web    <none>      2s",
    ]


# ------------------------------------------------------------------ #3

PENDING_BROKEN = """apiVersion: apps/v1
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
          image: ghcr.io/change-me-owner/k8s-lab-app:0.1.0
          ports:
            - containerPort: 8080
          resources:
            requests:
              cpu: "8"
              memory: "16Gi"
            limits:
              cpu: "8"
              memory: "16Gi"
"""


def validate_pending(doc: dict) -> tuple[bool, str]:
    if doc.get("kind") != "Deployment":
        return False, "The resource must be a Deployment."
    try:
        resources = doc["spec"]["template"]["spec"]["containers"][0]["resources"]
        requests = resources.get("requests") or {}
        cpu = parse_cpu(requests.get("cpu", "0"))
        memory = parse_memory_mib(requests.get("memory", "0Mi"))
    except (KeyError, IndexError, TypeError, ValueError):
        return False, "resources.requests is missing or malformed."
    if cpu <= NODE_CPU and memory <= NODE_MEMORY_MIB:
        return True, "The pod is scheduled and running on a node."
    return False, (
        "The pod is still Pending and has not been scheduled. Check the pod "
        "events and the node's available capacity."
    )


def simulate_pending(doc: dict) -> list[str]:
    try:
        requests = doc["spec"]["template"]["spec"]["containers"][0]["resources"]["requests"]
        cpu = parse_cpu(requests.get("cpu", "0"))
        memory = parse_memory_mib(requests.get("memory", "0Mi"))
        fits = cpu <= NODE_CPU and memory <= NODE_MEMORY_MIB
    except (KeyError, IndexError, TypeError, ValueError):
        return ["error: unable to parse resource requests"]
    if fits:
        return [
            "deployment.apps/web created",
            "",
            "NAME                   READY   STATUS    RESTARTS   AGE",
            "web-7c5b9f8d6e-xyz34   1/1     Running   0          3s",
        ]
    return [
        "deployment.apps/web created",
        "",
        "NAME                   READY   STATUS    RESTARTS   AGE",
        "web-7c5b9f8d6e-xyz34   0/1     Pending   0          4s",
        "",
        "Warning  FailedScheduling  0/1 nodes are available: "
        "1 Insufficient cpu, 1 Insufficient memory.",
    ]


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
        simulate=simulate_image,
    ),
    "service-no-endpoints": Challenge(
        slug="service-no-endpoints",
        title="Service returns 503",
        difficulty="Medium",
        points=120,
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
        validate=validate_service,
        simulate=simulate_service,
    ),
    "pending-resources": Challenge(
        slug="pending-resources",
        title="Pod never schedules",
        difficulty="Medium",
        points=150,
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
            "the Events section. Compare that with what the node can offer: "
            "'kubectl describe node'."
        ),
        broken_yaml=PENDING_BROKEN,
        setup_yaml="",
        target_kind="Deployment",
        target_name="web",
        validate=validate_pending,
        simulate=simulate_pending,
    ),
}


def get_challenge(slug: str) -> Challenge | None:
    return CHALLENGES.get(slug)
