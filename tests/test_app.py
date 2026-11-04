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
    assert "Masuk lab" in page.text


def test_dashboard_requires_login(client):
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_join_then_dashboard(client):
    join(client)
    page = client.get("/dashboard")
    assert page.status_code == 200
    assert "Daftar challenge" in page.text
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

    fixed = IMAGE_BROKEN.replace("0.0.0-tidak-ada", "0.1.0")
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
