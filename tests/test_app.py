"""Alur utama platform: masuk, apply, verifikasi, leaderboard."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.challenges import (
    CHALLENGES,
    COMMAND_BROKEN,
    IMAGE_BROKEN,
    PENDING_BROKEN,
    PORT_ENV_BROKEN,
    READINESS_BROKEN,
    SELECTOR_BROKEN,
    TARGET_PORT_BROKEN,
)
from app.main import app


@pytest.fixture()
def client():
    # TestClient sebagai context manager menjalankan lifespan (init_db).
    with TestClient(app) as test_client:
        yield test_client


PASSWORD = "correct-horse-42"


def join(
    client: TestClient,
    name: str | None = None,
    password: str = PASSWORD,
    intent: str = "register",
) -> str:
    name = name or f"user-{uuid.uuid4().hex[:8]}"
    response = client.post(
        "/join",
        data={"name": name, "password": password, "intent": intent},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return name


def login(client: TestClient, name: str, password: str = PASSWORD):
    return client.post(
        "/join",
        data={"name": name, "password": password, "intent": "login"},
        follow_redirects=False,
    )


def test_landing_page(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "Sign in" in page.text
    assert "Create account" in page.text


def test_dashboard_requires_login(client):
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_join_then_dashboard(client):
    join(client)
    page = client.get("/dashboard")
    assert page.status_code == 200
    assert "Challenges" in page.text
    # ketiga challenge tampil
    for challenge in CHALLENGES.values():
        assert challenge.title in page.text


def test_duplicate_registration_is_rejected(client):
    name = join(client)
    response = client.post(
        "/join",
        data={"name": name, "password": PASSWORD, "intent": "register"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "already taken" in response.text


def test_wrong_password_is_rejected(client):
    name = join(client)
    response = login(client, name, password="not-the-password")
    assert response.status_code == 200
    assert "Incorrect password" in response.text


def test_login_for_unknown_user_is_rejected(client):
    response = login(client, "does-not-exist-9f3a", password="whatever-123")
    assert response.status_code == 200
    assert "No account with that username" in response.text


def test_sign_in_again_keeps_leaderboard_score(client):
    name = join(client)
    client.get("/challenge/image-pull-backoff")
    fixed = IMAGE_BROKEN.replace("0.0.3", "0.1.0")
    assert client.post(
        "/api/challenge/image-pull-backoff/verify", json={"yaml": fixed}
    ).json()["passed"] is True

    client.post("/logout", follow_redirects=False)
    response = login(client, name)
    assert response.status_code == 303

    dashboard = client.get("/dashboard")
    assert name in dashboard.text
    board = client.get("/leaderboard")
    assert name in board.text


def test_apply_broken_image_shows_pull_error(client):
    join(client)
    client.get("/challenge/image-pull-backoff")  # memulai attempt
    response = client.post(
        "/api/challenge/image-pull-backoff/apply",
        json={"yaml": IMAGE_BROKEN},
    )
    assert response.status_code == 200
    assert "ImagePullBackOff" in response.json()["output"]


def test_verify_fails_for_broken_manifest(client):
    join(client)
    client.get("/challenge/image-pull-backoff")
    response = client.post(
        "/api/challenge/image-pull-backoff/verify",
        json={"yaml": IMAGE_BROKEN},
    )
    payload = response.json()
    assert payload["passed"] is False
    assert payload["tries"] == 1


def test_verify_passes_after_fix_and_scores(client):
    name = join(client)
    client.get("/challenge/image-pull-backoff")

    fixed = IMAGE_BROKEN.replace("0.0.3", "0.1.0")
    response = client.post(
        "/api/challenge/image-pull-backoff/verify",
        json={"yaml": fixed},
    )
    payload = response.json()
    assert payload["passed"] is True
    assert 20 <= payload["score"] <= CHALLENGES["image-pull-backoff"].points

    board = client.get("/leaderboard")
    assert name in board.text


def test_service_challenge_validator(client):
    join(client)
    client.get("/challenge/service-no-endpoints")

    broken = CHALLENGES["service-no-endpoints"].broken_yaml
    assert client.post(
        "/api/challenge/service-no-endpoints/verify", json={"yaml": broken}
    ).json()["passed"] is False

    fixed = broken.replace("app: web-v2", "app: web")
    payload = client.post(
        "/api/challenge/service-no-endpoints/verify", json={"yaml": fixed}
    ).json()
    assert payload["passed"] is True


def test_pending_challenge_validator(client):
    join(client)
    client.get("/challenge/pending-resources")
    broken = CHALLENGES["pending-resources"].broken_yaml
    assert client.post(
        "/api/challenge/pending-resources/verify", json={"yaml": broken}
    ).json()["passed"] is False

    fixed = broken.replace('cpu: "8"', 'cpu: "100m"').replace(
        'memory: "16Gi"', 'memory: "128Mi"'
    )
    assert client.post(
        "/api/challenge/pending-resources/verify", json={"yaml": fixed}
    ).json()["passed"] is True


def test_invalid_yaml_is_rejected(client):
    join(client)
    client.get("/challenge/image-pull-backoff")
    response = client.post(
        "/api/challenge/image-pull-backoff/apply",
        json={"yaml": "ini: [bukan yaml"},
    )
    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_verify_without_attempt_is_rejected(client):
    join(client)
    response = client.post(
        "/api/challenge/image-pull-backoff/verify",
        json={"yaml": IMAGE_BROKEN},
    )
    assert response.status_code == 409


# --------------------------------------------------------------- terminal

def test_terminal_help_lists_commands(client):
    join(client)
    client.get("/challenge/image-pull-backoff")
    response = client.post(
        "/api/challenge/image-pull-backoff/terminal", json={"command": "help"}
    )
    assert response.status_code == 200
    assert "kubectl get pods" in response.json()["output"]


def test_terminal_get_pods_shows_breakage(client):
    join(client)
    client.get("/challenge/image-pull-backoff")
    output = client.post(
        "/api/challenge/image-pull-backoff/terminal",
        json={"command": "kubectl get pods"},
    ).json()["output"]
    assert "ImagePullBackOff" in output


def test_terminal_set_image_fixes_and_verifies(client):
    join(client)
    client.get("/challenge/image-pull-backoff")

    command = (
        "kubectl set image deployment/web "
        "web=ghcr.io/change-me-owner/k8s-lab-app:0.1.0"
    )
    output = client.post(
        "/api/challenge/image-pull-backoff/terminal", json={"command": command}
    ).json()["output"]
    assert "image updated" in output

    pods = client.post(
        "/api/challenge/image-pull-backoff/terminal",
        json={"command": "kubectl get pods"},
    ).json()["output"]
    assert "Running" in pods

    verified = client.post(
        "/api/challenge/image-pull-backoff/verify", json={"source": "terminal"}
    ).json()
    assert verified["passed"] is True
    assert verified["score"] > 0


def test_terminal_scale_changes_replicas(client):
    join(client)
    client.get("/challenge/pending-resources")
    output = client.post(
        "/api/challenge/pending-resources/terminal",
        json={"command": "kubectl scale deployment/web --replicas=3"},
    ).json()["output"]
    assert "scaled" in output

    describe = client.post(
        "/api/challenge/pending-resources/terminal",
        json={"command": "kubectl describe deployment web"},
    ).json()["output"]
    assert "Replicas:           3" in describe


def test_terminal_rejects_unknown_verb(client):
    join(client)
    client.get("/challenge/image-pull-backoff")
    output = client.post(
        "/api/challenge/image-pull-backoff/terminal",
        json={"command": "kubectl drain node-1"},
    ).json()["output"]
    assert "not supported" in output


def test_terminal_and_editor_share_sandbox(client):
    join(client)
    client.get("/challenge/image-pull-backoff")

    client.post(
        "/api/challenge/image-pull-backoff/terminal",
        json={"command": "kubectl scale deployment/web --replicas=2"},
    )
    describe = client.post(
        "/api/challenge/image-pull-backoff/terminal",
        json={"command": "kubectl describe deployment web"},
    ).json()["output"]
    assert "Replicas:           2" in describe


# --------------------------------------------------------------- new labs

def run(client: TestClient, slug: str, command: str) -> str:
    return client.post(
        f"/api/challenge/{slug}/terminal", json={"command": command}
    ).json()["output"]


def test_catalog_has_ten_challenges():
    assert len(CHALLENGES) == 10


def test_every_broken_manifest_really_fails():
    import yaml

    for challenge in CHALLENGES.values():
        document = yaml.safe_load(challenge.broken_yaml)
        passed, _ = challenge.validate(document)
        assert passed is False, challenge.slug


def test_command_override_fixed_from_editor(client):
    join(client)
    client.get("/challenge/command-override")

    fixed = COMMAND_BROKEN.replace(
        "          command:\n            - python3\n            - server.py\n", ""
    )
    payload = client.post(
        "/api/challenge/command-override/verify", json={"yaml": fixed}
    ).json()
    assert payload["passed"] is True


def test_oom_fix_via_terminal_set_resources(client):
    join(client)
    client.get("/challenge/oom-killed")

    output = run(
        client,
        "oom-killed",
        "kubectl set resources deployment/web --limits=memory=256Mi",
    )
    assert "resource requirements updated" in output
    assert "Running" in run(client, "oom-killed", "kubectl get pods")
    assert client.post(
        "/api/challenge/oom-killed/verify", json={"source": "terminal"}
    ).json()["passed"] is True


def test_readiness_probe_fixed_by_path(client):
    join(client)
    client.get("/challenge/readiness-probe")

    describe = run(client, "readiness-probe", "kubectl describe pod web-6b8c9d7f42-t5v6w")
    assert "Readiness probe failed" in describe

    fixed = READINESS_BROKEN.replace("path: /health\n", "path: /healthz\n")
    assert client.post(
        "/api/challenge/readiness-probe/verify", json={"yaml": fixed}
    ).json()["passed"] is True


def test_port_env_fix_via_terminal_set_env(client):
    join(client)
    client.get("/challenge/port-env")

    assert "listening on 0.0.0.0:9090" in run(
        client, "port-env", "kubectl logs web-7d9e8f6a21-c3d4e"
    )
    run(client, "port-env", "kubectl set env deployment/web PORT=8080")
    assert "1/1" in run(client, "port-env", "kubectl get pods")
    assert client.post(
        "/api/challenge/port-env/verify", json={"source": "terminal"}
    ).json()["passed"] is True


def test_target_port_fixed_in_manifest(client):
    join(client)
    client.get("/challenge/service-target-port")

    fixed = TARGET_PORT_BROKEN.replace("targetPort: 8081", "targetPort: 8080")
    assert client.post(
        "/api/challenge/service-target-port/verify", json={"yaml": fixed}
    ).json()["passed"] is True


def test_selector_manifest_is_rejected_by_apply(client):
    join(client)
    client.get("/challenge/deployment-selector")

    response = client.post(
        "/api/challenge/deployment-selector/apply", json={"yaml": SELECTOR_BROKEN}
    )
    assert response.json()["ok"] is False
    assert "does not match" in response.json()["output"]

    fixed = SELECTOR_BROKEN.replace("app: web-api", "app: web")
    assert client.post(
        "/api/challenge/deployment-selector/verify", json={"yaml": fixed}
    ).json()["passed"] is True


def test_paused_rollout_resumed_via_terminal(client):
    join(client)
    client.get("/challenge/deployment-paused")

    status = run(client, "deployment-paused", "kubectl rollout status deployment/web")
    assert "Waiting" in status

    assert "resumed" in run(client, "deployment-paused", "kubectl rollout resume deployment/web")
    assert "Running" in run(client, "deployment-paused", "kubectl get pods")
    assert client.post(
        "/api/challenge/deployment-paused/verify", json={"source": "terminal"}
    ).json()["passed"] is True


def test_pending_terminal_still_works(client):
    join(client)
    client.get("/challenge/pending-resources")

    run(
        client,
        "pending-resources",
        "kubectl set resources deployment/web --requests=cpu=100m,memory=128Mi",
    )
    assert client.post(
        "/api/challenge/pending-resources/verify", json={"source": "terminal"}
    ).json()["passed"] is True


# ------------------------------------------------------- manifest safety

HOSTPATH_MANIFEST = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
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
          image: nginx
          volumeMounts:
            - name: host
              mountPath: /host
      volumes:
        - name: host
          hostPath:
            path: /
"""


def test_manifest_with_hostpath_is_rejected(client):
    join(client)
    client.get("/challenge/image-pull-backoff")
    response = client.post(
        "/api/challenge/image-pull-backoff/apply",
        json={"yaml": HOSTPATH_MANIFEST},
    )
    assert response.status_code == 400
    assert "hostPath" in response.json()["output"]


def test_manifest_with_privileged_is_rejected(client):
    join(client)
    client.get("/challenge/image-pull-backoff")
    privileged = HOSTPATH_MANIFEST.replace(
        "          volumeMounts:\n            - name: host\n              mountPath: /host\n",
        "          securityContext:\n            privileged: true\n",
    ).replace(
        "      volumes:\n        - name: host\n          hostPath:\n            path: /\n",
        "",
    )
    response = client.post(
        "/api/challenge/image-pull-backoff/apply",
        json={"yaml": privileged},
    )
    assert response.status_code == 400
    assert "privileged" in response.json()["output"]


def test_manifest_with_disallowed_kind_is_rejected(client):
    join(client)
    client.get("/challenge/image-pull-backoff")
    response = client.post(
        "/api/challenge/image-pull-backoff/apply",
        json={
            "yaml": (
                "apiVersion: v1\nkind: Pod\nmetadata:\n  name: sneaky\n"
                "spec:\n  containers:\n    - name: c\n      image: nginx\n"
            )
        },
    )
    assert response.status_code == 400
    assert "not allowed" in response.json()["output"]
