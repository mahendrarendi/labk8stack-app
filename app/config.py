"""Konfigurasi platform, dibaca dari environment variable."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings:
    app_name: str = "K8s Lab"
    tagline: str = "Belajar Kubernetes dengan memperbaiki cluster yang rusak"

    # WAJIB diganti di produksi. Dipakai untuk menandatangani cookie sesi.
    secret_key: str = os.environ.get("SECRET_KEY", "dev-insecure-change-me")

    db_path: Path = Path(os.environ.get("DB_PATH", str(BASE_DIR / "data" / "lab.db")))

    # fake        -> simulator, tidak butuh cluster (default, aman untuk demo)
    # kubernetes  -> membuat namespace + resource asli lewat kubectl
    mode: str = os.environ.get("LAB_MODE", "fake").lower()

    session_max_age: int = int(os.environ.get("SESSION_MAX_AGE", str(60 * 60 * 24 * 7)))
    leaderboard_days: int = int(os.environ.get("LEADERBOARD_DAYS", "7"))

    # Namespace setiap user di mode kubernetes: lab-u<id>-<slug>
    namespace_prefix: str = os.environ.get("LAB_NAMESPACE_PREFIX", "lab-u")

    # Path kubeconfig yang dipakai kubectl (kosong = default ~/.kube/config
    # atau in-cluster ServiceAccount bila platform berjalan sebagai Pod).
    kubeconfig: str = os.environ.get("LAB_KUBECONFIG", "")

    # Jalankan perintah user sebagai ServiceAccount per-namespace
    # (system:serviceaccount:lab-u<id>-<slug>:lab-runner) alih-alih identitas platform.
    impersonate: bool = os.environ.get("LAB_IMPERSONATE", "true").lower() not in {
        "0",
        "false",
        "no",
    }
    runner_service_account: str = os.environ.get("LAB_RUNNER_SA", "lab-runner")
    # ClusterRole yang di-bind ke ServiceAccount tersebut.
    runner_cluster_role: str = os.environ.get("LAB_RUNNER_CLUSTERROLE", "lab-user")

    # Image aplikasi demo. Ganti ke image yang benar-benar ada di registry
    # cluster Anda sebelum memakai mode kubernetes.
    app_image: str = os.environ.get(
        "LAB_APP_IMAGE", "ghcr.io/change-me-owner/k8s-lab-app:0.1.0"
    )
    # Tag yang dianggap valid untuk challenge ImagePullBackOff.
    app_good_tags: str = os.environ.get(
        "LAB_APP_GOOD_TAGS", "0.1.0,v0.1.0,latest"
    )


settings = Settings()
