"""Docker runtime tests (TC-21 .. TC-24). Need WISECOW_IMAGE and a working Docker.
Each test gets its own hardened container (read-only rootfs, all capabilities dropped,
no-new-privileges) that is removed afterwards, even if the test fails.
"""
import json
import subprocess
import time

import pytest

from conftest import DOCKER, wait_until_ready

pytestmark = pytest.mark.docker


def docker(*args, timeout=60):
    return subprocess.run(DOCKER + list(args), capture_output=True, text=True, timeout=timeout)


def test_container_runs_as_non_root(container):
    """TC-21: the process runs as uid 10001, not root."""
    cid, url = container
    wait_until_ready(url)
    assert docker("exec", cid, "id", "-u").stdout.strip() == "10001"


def test_root_filesystem_is_read_only_and_app_still_serves(container):
    """TC-22: with --read-only the app still serves, and its own script cannot be modified."""
    cid, url = container
    wait_until_ready(url)
    p = docker("exec", cid, "sh", "-c", "echo x >> /app/wisecow.sh")
    assert p.returncode != 0


def test_healthcheck_reports_healthy(container):
    """TC-23: the image HEALTHCHECK (a real HTTP request) turns the container healthy."""
    cid, _ = container
    deadline = time.monotonic() + 60
    status = None
    while time.monotonic() < deadline:
        status = json.loads(docker("inspect", cid).stdout)[0]["State"].get("Health", {}).get("Status")
        if status == "healthy":
            break
        time.sleep(1)
    assert status == "healthy"


def test_graceful_stop_is_fast(container):
    """TC-24: SIGTERM stops the container quickly (socat is PID 1 and handles it), no 10 s kill."""
    cid, url = container
    wait_until_ready(url)
    started = time.monotonic()
    assert docker("stop", "-t", "10", cid).returncode == 0
    assert time.monotonic() - started < 5
    exit_code = json.loads(docker("inspect", cid).stdout)[0]["State"]["ExitCode"]
    assert exit_code != 137       # 137 would mean it had to be SIGKILLed
