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

    # Namespace setiap user di mode kubernetes: lab-u<id>
    namespace_prefix: str = os.environ.get("LAB_NAMESPACE_PREFIX", "lab-u")


settings = Settings()
