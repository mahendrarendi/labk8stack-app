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
          image: ghcr.io/change-me-owner/k8s-lab-app:0.0.0-not-found
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
    tag = _image_tag(image)
    if tag in GOOD_IMAGE_TAGS:
        return True, f"Image '{image}' can be pulled. The Deployment is ready."
    return False, f"Tag '{tag}' does not exist in the registry. Pod is still ImagePullBackOff."


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
        return True, "Selector matches the pod labels, Endpoints are populated."
    return False, (
        "Service selector does not match the pod labels (app: web). "
        "Endpoints stay empty, so requests return 503."
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
        "",
        "Warning: selector 'app=web-v2' matches no pod (pods are labeled app=web)",
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
        return True, f"Requests of {cpu:g} CPU / {memory:g}Mi fit the node. Pod is scheduled."
    return False, (
        f"Requests of {cpu:g} CPU / {memory:g}Mi exceed node capacity "
        f"({NODE_CPU:g} CPU / {NODE_MEMORY_MIB}Mi). Pod stays Pending."
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
        title="ImagePullBackOff",
        difficulty="Easy",
        points=100,
        duration_seconds=600,
        summary="Pods never start because the image cannot be pulled.",
        story=(
            "Your team just shipped a new deployment, but the pod is stuck in "
            "ImagePullBackOff. There are no container logs at all because the "
            "container was never created."
        ),
        objective=(
            "Fix the image tag on Deployment 'web' so the image can be pulled "
            "and the pod reaches Running."
        ),
        hint=(
            "Look at the image field. Tag 0.0.0-not-found is intentionally "
            "absent from the registry. Use tag 0.1.0."
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
        title="Service 503 / No Endpoints",
        difficulty="Medium",
        points=120,
        duration_seconds=600,
        summary="Pods are healthy, but the Service has no Endpoints.",
        story=(
            "Two pods are running fine, yet every request through the Service "
            "returns 503. There is nothing in the application logs because "
            "requests never reach a pod."
        ),
        objective="Fix the Service selector so it matches the pod labels.",
        hint=(
            "Compare spec.selector on the Service with the pod labels "
            "(app: web). Empty Endpoints mean the selector matches nothing."
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
        title="Pod Pending",
        difficulty="Medium",
        points=150,
        duration_seconds=600,
        summary="The pod is not scheduled because requests are too large.",
        story=(
            "The Deployment never becomes Running. Events show "
            "FailedScheduling: Insufficient cpu and Insufficient memory."
        ),
        objective=(
            "Lower the resource requests so they fit the node "
            f"({NODE_CPU:g} CPU, {NODE_MEMORY_MIB}Mi)."
        ),
        hint=(
            "requests.cpu 8 and memory 16Gi are far too large. Try 100m and 128Mi."
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
