"""Set the test environment before importing application modules."""

import os
import pathlib
import tempfile

_tmp = pathlib.Path(tempfile.mkdtemp(prefix="k8s-lab-test-"))
os.environ["SECRET_KEY"] = "test-secret-only"
os.environ["LAB_MODE"] = "fake"
# Do not read the real .env during tests.
os.environ["LAB_ENV_FILE"] = str(_tmp / "missing.env")
# Use a database separate from the application database for tests.
if test_database_url := os.environ.get("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = test_database_url
else:
    os.environ.pop("DATABASE_URL", None)
