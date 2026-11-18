"""Helpers for the tests that run a real database in Docker (marker `db`).

Every environment is disposable: a uniquely named Compose project, a random loopback port and its own
copy of the generated application (node_modules is shared through a symlink). Only the project a test
created is ever torn down, together with its volume.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import uuid
from pathlib import Path

import pytest


def plain(text: str) -> str:
    """Terminal output without colour codes: vitest and the CLIs colour their output when CI is set."""
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def listeners(port: int) -> str:
    """Addresses listening on `port`: `ss` on Linux (no privileges needed), `lsof` on macOS."""
    for command in (["ss", "-ltnH", f"sport = :{port}"], ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"]):
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    return ""


def docker_ready() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=30, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def require_docker() -> None:
    """Fail (CI sets REQUIRE_DOCKER=1) or skip with a stated reason when Docker is not available."""
    if docker_ready():
        return
    if os.environ.get("REQUIRE_DOCKER") == "1":
        pytest.fail("Docker is required for the database tests but is not available")
    pytest.skip("Docker is not available on this machine (the database tests need a running Docker daemon)")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run(command: list[str], cwd: Path, env: dict[str, str] | None = None, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True, text=True, timeout=timeout, check=False)


def install(project: Path) -> None:
    result = run(["npm", "ci", "--no-audit", "--no-fund"], project)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]


class LocalApp:
    """A disposable copy of a generated, installed application with its own Compose project and port."""

    def __init__(self, installed: Path, destination: Path) -> None:
        self.root = destination
        shutil.copytree(installed, destination, symlinks=True, ignore=shutil.ignore_patterns("node_modules", ".next", ".env.local"))
        (destination / "node_modules").symlink_to(installed / "node_modules")
        self.project = f"wptest-{uuid.uuid4().hex[:10]}"
        self.port = free_port()
        self.env = {"COMPOSE_PROJECT_NAME": self.project, "POSTGRES_PORT": str(self.port)}

    def npm(self, *args: str, timeout: int = 600) -> subprocess.CompletedProcess[str]:
        return run(["npm", "run", "--silent", *args], self.root, self.env, timeout)

    def node(self, code: str) -> subprocess.CompletedProcess[str]:
        return run(["node", "--input-type=module", "-e", code], self.root, self.env)

    def compose(self, *args: str) -> subprocess.CompletedProcess[str]:
        return run(["docker", "compose", "--env-file", ".env.local", *args], self.root, self.env)

    def psql(self, sql: str) -> str:
        result = self.compose("exec", "-T", "db", "sh", "-c", 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "$1"', "sh", sql)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def start(self) -> None:
        for command in ("db:init", "db:up"):
            result = self.npm(command)
            assert result.returncode == 0, f"{command}: {result.stdout[-1500:]}{result.stderr[-1500:]}"

    def env_file(self) -> dict[str, str]:
        lines = (self.root / ".env.local").read_text().splitlines()
        return dict(line.split("=", 1) for line in lines if line and not line.startswith("#"))

    def database_url(self) -> str:
        return self.env_file()["DATABASE_URL"]

    def destroy(self) -> None:
        """Remove this test's containers, network and volume. Scoped by its unique project name."""
        if (self.root / ".env.local").exists():
            self.compose("-p", self.project, "down", "--volumes", "--remove-orphans")
