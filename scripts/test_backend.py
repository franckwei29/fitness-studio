"""Run backend regression tests against a dedicated local Docker project.

Usage: .venv/bin/python scripts/test_backend.py [pytest arguments]
Requires the backend development environment and Docker Compose.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import URL


def main():
    root = Path(__file__).resolve().parents[1]
    docker = shutil.which("docker")
    if not docker:
        raise SystemExit(
            "Docker is required. Add the Docker CLI to PATH before running tests."
        )
    private = dotenv_values(root / ".env")
    password = private.get("DATABASE_PASSWORD")
    root_password = private.get("DATABASE_ROOT_PASSWORD")
    if not password or not root_password:
        raise SystemExit(
            "Configure database credentials in the private application .env first."
        )
    env = os.environ | {
        "DATABASE_PASSWORD": password,
        "DATABASE_ROOT_PASSWORD": root_password,
        "DATABASE_NAME": "fitness_studio_test",
        "DATABASE_PORT": "33317",
        "REDIS_PORT": "36380",
        "DATABASE_URL": URL.create(
            "mysql+pymysql",
            username="studio",
            password=password,
            host="127.0.0.1",
            port=33317,
            database="fitness_studio_test",
        ).render_as_string(hide_password=False),
        "REDIS_URL": "redis://127.0.0.1:36380/0",
        "GEMINI_API_KEY": "",
        "OPENAI_API_KEY": "",
        "MODEL_PROVIDER": "gemini",
        "MODEL_CALLS_ENABLED": "false",
        "OWNER_EMAIL": "test-owner@example.test",
        "OWNER_PASSWORD": "Isolated-regression-test-123!",
        "SESSION_SECRET": "isolated-regression-session-secret-not-for-deployment",
    }
    subprocess.run(
        [
            docker,
            "compose",
            "-p",
            "fitness-studio-test",
            "-f",
            str(root / "compose.yaml"),
            "up",
            "-d",
            "--wait",
            "mysql",
            "redis",
        ],
        cwd=root,
        env=env,
        check=True,
    )
    backend = root / "backend"
    for command in (
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        [sys.executable, "-m", "app.bootstrap"],
    ):
        subprocess.run(command, cwd=backend, env=env, check=True)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *(sys.argv[1:] or ["-q"])],
        cwd=backend,
        env=env,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
