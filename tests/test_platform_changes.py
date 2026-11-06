"""Checks for sandbox lifecycle and live cluster verification."""

from datetime import datetime, timedelta, timezone
import json
import uuid
from pathlib import Path

import yaml

from app import cleanup
from app import db
from app.challenges import CHALLENGES
from app.engine import ApplyResult
from app.engine.kubernetes import KubernetesEngine, _PROVISIONING


def test_provisioning_keeps_broken_pending_scenario_admissible():
    docs = list(yaml.safe_load_all(_PROVISIONING.format(
        service_account="lab-runner", namespace="lab-u1-pending-resources",
        cluster_role="lab-user",
    )))
    by_kind = {document["kind"]: document for document in docs}
    assert {"ResourceQuota", "LimitRange", "NetworkPolicy"} <= by_kind.keys()
    assert int(by_kind["ResourceQuota"]["spec"]["hard"]["requests.cpu"]) >= 8
    policy = by_kind["NetworkPolicy"]["spec"]
    assert policy["policyTypes"] == ["Ingress", "Egress"]
    assert policy["egress"][0]["to"] == [{"podSelector": {}}]
    assert policy["egress"][1]["ports"] == [
        {"protocol": "UDP", "port": 53},
        {"protocol": "TCP", "port": 53},
    ]


def test_expired_sandbox_detected_even_when_user_is_active(monkeypatch):
    now = datetime.now(timezone.utc)
    items = [
        {"metadata": {"name": "lab-u7-image-pull-backoff", "annotations": {
            cleanup.EXPIRY_ANNOTATION: (now - timedelta(minutes=1)).isoformat()
        }}},
        {"metadata": {"name": "lab-u8-image-pull-backoff", "annotations": {
            cleanup.EXPIRY_ANNOTATION: (now + timedelta(hours=1)).isoformat()
        }}},
    ]
    monkeypatch.setattr(cleanup, "_kubectl", lambda *args: (True, json.dumps({"items": items})))
    assert cleanup.expired_sandbox_namespaces(now) == ["lab-u7-image-pull-backoff"]


def test_completed_attempt_namespace_is_selected_for_migration_cleanup():
    namespaces = ["lab-u7-image-pull-backoff", "lab-u7-oom-killed"]
    completed = {(7, "image-pull-backoff")}
    assert cleanup.completed_namespace_names(namespaces, completed) == [
        "lab-u7-image-pull-backoff"
    ]


def test_service_check_requires_ready_endpoint_on_application_port(monkeypatch):
    monkeypatch.setattr("app.engine.kubernetes.which", lambda _: "/usr/bin/kubectl")
    engine = KubernetesEngine()
    result = {"subsets": [{"addresses": [{"ip": "10.0.0.1"}], "ports": [{"port": 8081}]}]}
    monkeypatch.setattr(engine, "_run", lambda *args, **kwargs: ApplyResult(True, json.dumps(result)))
    challenge = CHALLENGES["service-target-port"]
    assert engine.check(1, challenge)[0] is False
    result["subsets"][0]["ports"][0]["port"] = 8080
    assert engine.check(1, challenge)[0] is True


def test_deployment_check_waits_for_rollout(monkeypatch):
    monkeypatch.setattr("app.engine.kubernetes.which", lambda _: "/usr/bin/kubectl")
    engine = KubernetesEngine()
    monkeypatch.setattr(engine, "_run", lambda *args, **kwargs: ApplyResult(False, "not ready"))
    assert engine.check(1, CHALLENGES["image-pull-backoff"]) == (False, "not ready")


def test_intentionally_invalid_deployment_still_starts_challenge(monkeypatch):
    monkeypatch.setattr("app.engine.kubernetes.which", lambda _: "/usr/bin/kubectl")
    engine = KubernetesEngine()
    monkeypatch.setattr(engine, "_ensure_namespace", lambda _: None)
    monkeypatch.setattr(engine, "_clean", lambda _: ApplyResult(True, "clean"))
    monkeypatch.setattr(engine, "_run", lambda *args, **kwargs: ApplyResult(False, "invalid selector"))
    result = engine.reset(1, CHALLENGES["deployment-selector"])
    assert result.ok is True


def test_terminal_cannot_load_unchecked_manifest(monkeypatch):
    monkeypatch.setattr("app.engine.kubernetes.which", lambda _: "/usr/bin/kubectl")
    engine = KubernetesEngine()
    output = engine.terminal(1, CHALLENGES["image-pull-backoff"],
                             "kubectl create -f https://example.com/pod.yaml")
    assert "disabled" in output


def test_provisioner_cannot_impersonate_arbitrary_users():
    docs = list(yaml.safe_load_all((Path(__file__).parents[1] / "deploy" / "rbac.yaml").read_text()))
    role = next(doc for doc in docs if doc["kind"] == "ClusterRole"
                and doc["metadata"]["name"] == "lab-provisioner")
    impersonation = [rule for rule in role["rules"] if "impersonate" in rule["verbs"]]
    assert impersonation == [{
        "apiGroups": [""], "resources": ["serviceaccounts"],
        "resourceNames": ["lab-runner"], "verbs": ["impersonate"],
    }]


def test_verify_rejects_editor_changes_without_applying(monkeypatch):
    import app.main as main
    from app.challenges import IMAGE_BROKEN

    fixed = IMAGE_BROKEN.replace("0.0.3", "0.1.0")
    monkeypatch.setattr(main, "_guard", lambda _: ({"id": 123}, None))
    monkeypatch.setattr(main.db, "active_attempt", lambda *_: {
        "id": 99,
        "started_at": db.iso(db.utcnow()),
        "applied_manifest_hash": None,
    })

    class EngineWithoutApply:
        def apply(self, *_):
            raise AssertionError("Verify must never apply a manifest")

    monkeypatch.setattr(main, "get_engine", EngineWithoutApply)
    response = main.api_verify(None, "image-pull-backoff", main.SubmitBody(yaml=fixed))
    assert response.status_code == 409
    assert b"Apply the current manifest" in response.body


def test_apply_then_verify_flow_without_http_threadpool(monkeypatch):
    import app.main as main
    from app.challenges import IMAGE_BROKEN

    db.init_db()
    user = db.create_user(f"direct-{uuid.uuid4().hex[:10]}", "test-hash")
    challenge = CHALLENGES["image-pull-backoff"]
    engine = main.get_engine()
    engine.reset(user["id"], challenge)
    attempt = db.start_attempt(user["id"], challenge.slug)
    monkeypatch.setattr(main, "_guard", lambda _: (user, None))
    fixed = IMAGE_BROKEN.replace("0.0.3", "0.1.0")

    unsent = main.api_verify(None, challenge.slug, main.SubmitBody(yaml=fixed))
    assert unsent.status_code == 409
    assert db.active_attempt(user["id"], challenge.slug)["tries"] == 0

    applied = main.api_apply(None, challenge.slug, main.SubmitBody(yaml=fixed))
    assert applied.status_code == 200
    assert db.active_attempt(user["id"], challenge.slug)["applied_manifest_hash"]

    result = main.api_verify(None, challenge.slug, main.SubmitBody(yaml=fixed))
    assert json.loads(result.body)["passed"] is True
    assert db.active_attempt(user["id"], challenge.slug) is None
