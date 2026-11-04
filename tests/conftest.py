"""Pastikan environment test diset SEBELUM modul app diimpor."""

import os
import pathlib
import tempfile

_tmp = pathlib.Path(tempfile.mkdtemp(prefix="k8s-lab-test-"))
os.environ["DB_PATH"] = str(_tmp / "test.db")
os.environ["SECRET_KEY"] = "test-secret-only"
os.environ["LAB_MODE"] = "fake"
