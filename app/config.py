"""Platform configuration read from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env for local uvicorn runs. Docker Compose also reads it for variable
# interpolation and passes the values into the container. Existing environment
# variables (for example, from systemd or CI) take precedence.
load_dotenv(os.environ.get("LAB_ENV_FILE", str(BASE_DIR / ".env")), override=False)


class Settings:
    app_name: str = "LabK8stack"
    tagline: str = "Learn Kubernetes by troubleshooting broken workloads"

    # Replace in production; this signs session cookies.
    secret_key: str = os.environ.get("SECRET_KEY", "dev-insecure-change-me")

    # Required outside Docker Compose. Compose builds this URL from the
    # PostgreSQL database, username, and password settings.
    database_url: str = os.environ.get("DATABASE_URL", "").strip()

    # fake        -> local simulator without a cluster
    # kubernetes  -> create real namespaces and resources through kubectl
    mode: str = os.environ.get("LAB_MODE", "fake").lower()

    session_max_age: int = int(os.environ.get("SESSION_MAX_AGE", str(60 * 60 * 24 * 7)))
    cookie_secure: bool = os.environ.get("COOKIE_SECURE", "false").lower() in {"1", "true", "yes"}
    leaderboard_days: int = int(os.environ.get("LEADERBOARD_DAYS", "7"))

    # Inactivity threshold used by `python -m app.cleanup`.
    sandbox_idle_days: int = int(os.environ.get("LAB_SANDBOX_IDLE_DAYS", "30"))
    sandbox_grace_minutes: int = int(os.environ.get("LAB_SANDBOX_GRACE_MINUTES", "60"))
    auto_cleanup: bool = os.environ.get("LAB_AUTO_CLEANUP", "true").lower() in {
        "1", "true", "yes",
    }

    # Namespace prefix for each user in Kubernetes mode: lab-u<id>-<slug>.
    namespace_prefix: str = os.environ.get("LAB_NAMESPACE_PREFIX", "lab-u")

    # Optional kubeconfig path. The Helm chart sets KUBECONFIG directly for
    # Pods because impersonated kubectl commands need explicit configuration.
    kubeconfig: str = os.environ.get("LAB_KUBECONFIG", "")

    # Run learner commands as the sandbox ServiceAccount.
    # Kubernetes mode rejects false.
    impersonate: bool = os.environ.get("LAB_IMPERSONATE", "true").lower() not in {
        "0",
        "false",
        "no",
    }
    runner_service_account: str = os.environ.get("LAB_RUNNER_SA", "lab-runner")
    # ClusterRole bound to each sandbox ServiceAccount.
    runner_cluster_role: str = os.environ.get("LAB_RUNNER_CLUSTERROLE", "lab-user")

    # Default challenge image placeholder. Replace it with an image available
    # to the cluster before using Kubernetes mode.
    app_image: str = os.environ.get(
        "LAB_APP_IMAGE", "ghcr.io/change-me-owner/k8s-lab-app:0.1.0"
    )
    # Tags accepted by the ImagePullBackOff challenge.
    app_good_tags: str = os.environ.get(
        "LAB_APP_GOOD_TAGS", "0.1.0,v0.1.0,latest"
    )


settings = Settings()
