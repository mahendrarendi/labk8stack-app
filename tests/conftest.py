"""Pastikan environment test diset SEBELUM modul app diimpor."""

import os
import pathlib
import tempfile

_tmp = pathlib.Path(tempfile.mkdtemp(prefix="k8s-lab-test-"))
os.environ["SECRET_KEY"] = "test-secret-only"
os.environ["LAB_MODE"] = "fake"
# Jangan baca .env asli saat test.
os.environ["LAB_ENV_FILE"] = str(_tmp / "missing.env")
# Test database harus terpisah dari database aplikasi.
if test_database_url := os.environ.get("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = test_database_url
else:
    os.environ.pop("DATABASE_URL", None)
