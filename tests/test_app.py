"""Alur utama platform: masuk, apply, verifikasi, leaderboard."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.challenges import CHALLENGES, IMAGE_BROKEN
from app.main import app


@pytest.fixture()
def client():
    # TestClient sebagai context manager menjalankan lifespan (init_db).
    with TestClient(app) as test_client:
        yield test_client


def join(client: TestClient, name: str | None = None) -> str:
    name = name or f"user-{uuid.uuid4().hex[:8]}"
    response = client.post("/join", data={"name": name}, follow_redirects=False)
    assert response.status_code == 303
    return name


def test_landing_page(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "Enter lab" in page.text


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

    fixed = IMAGE_BROKEN.replace("0.0.0-not-found", "0.1.0")
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
