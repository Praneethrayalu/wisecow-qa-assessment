"""Shared fixtures for the Wisecow QA suite.

Where the app comes from (first match wins):
  WISECOW_URL     test an app that is already running (docker run, kubectl port-forward, ...)
  WISECOW_IMAGE   start this image with Docker for the session (hardened flags, random port)
                  and remove the container afterwards. Also enables the Docker runtime tests.
If neither is set, live tests are reported as SKIPPED ("not run: no endpoint") - unless
WISECOW_REQUIRE_LIVE=1, in which case they FAIL. CI sets WISECOW_REQUIRE_LIVE=1, so a missing
app can never turn into a green run.

Cluster / TLS tests (all optional, skipped with a reason when unset; WISECOW_REQUIRE_CLUSTER=1 fails instead):
  WISECOW_TLS_URL           HTTPS URL through the ingress, e.g. https://wisecow.local:8443
  WISECOW_CA_CERT           CA file that signed the ingress certificate (cert-manager secret ca.crt)
  WISECOW_HTTP_INGRESS_URL  plain-HTTP ingress URL, for the redirect test
  KUBECTL                   kubectl command for live-cluster checks (e.g. "sudo kubectl")
Timeouts: WISECOW_TIMEOUT (seconds per request, default 10).
"""
import os
import pathlib
import shlex
import shutil
import socket
import subprocess
import time

import pytest
import requests

REPO = pathlib.Path(__file__).resolve().parent.parent
TIMEOUT = float(os.environ.get("WISECOW_TIMEOUT", "10"))
REQUIRE_LIVE = os.environ.get("WISECOW_REQUIRE_LIVE") == "1"
REQUIRE_CLUSTER = os.environ.get("WISECOW_REQUIRE_CLUSTER") == "1"
DOCKER = shlex.split(os.environ.get("DOCKER", "docker"))
KUBECTL = shlex.split(os.environ.get("KUBECTL", "kubectl"))


def not_available(reason, required):
    """Missing infrastructure: FAIL when the run requires it, otherwise SKIP with the reason."""
    if required:
        pytest.fail(f"required but not available: {reason}", pytrace=False)
    pytest.skip(f"not run: {reason}")


def wait_until_ready(url, seconds=30):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        try:
            r = requests.get(url + "/", timeout=3)
            if r.status_code == 200:
                return
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = type(e).__name__
        time.sleep(0.5)
    raise RuntimeError(f"wisecow at {url} not ready after {seconds}s (last: {last})")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def docker_ok():
    if not shutil.which(DOCKER[-1]) and not shutil.which(DOCKER[0]):
        return False
    return subprocess.run(DOCKER + ["info"], capture_output=True, timeout=20).returncode == 0


def start_container(image, name, extra=()):
    port = free_port()
    cmd = DOCKER + ["run", "-d", "--name", name, "--read-only", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges", "-p", f"127.0.0.1:{port}:4499", *extra, image]
    cid = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60).stdout.strip()
    return cid, f"http://127.0.0.1:{port}"


def remove_container(cid):
    subprocess.run(DOCKER + ["rm", "-f", cid], capture_output=True, timeout=60)


@pytest.fixture(scope="session")
def repo():
    return REPO


@pytest.fixture(scope="session")
def base_url():
    """URL of a running wisecow; starts (and later removes) a container when WISECOW_IMAGE is set."""
    url = os.environ.get("WISECOW_URL", "").rstrip("/")
    if url:
        try:
            wait_until_ready(url, 15)
        except RuntimeError as e:
            not_available(str(e), REQUIRE_LIVE)
        yield url
        return
    image = os.environ.get("WISECOW_IMAGE")
    if not image:
        not_available("no endpoint (set WISECOW_URL or WISECOW_IMAGE)", REQUIRE_LIVE)
    if not docker_ok():
        not_available("WISECOW_IMAGE set but Docker is not usable", REQUIRE_LIVE)
    cid, url = start_container(image, f"wisecow-qa-{os.getpid()}")
    try:
        wait_until_ready(url, 30)
        yield url
    finally:
        remove_container(cid)


@pytest.fixture(scope="session")
def host_port(base_url):
    from urllib.parse import urlsplit
    parts = urlsplit(base_url)
    return parts.hostname, parts.port or 80


@pytest.fixture
def raw_http(host_port):
    """Send raw bytes over TCP and return the whole response (for malformed-request tests)."""
    opened = []

    def send(payload: bytes, timeout=TIMEOUT, half_close=True):
        s = socket.create_connection(host_port, timeout=timeout)
        opened.append(s)
        if payload:
            s.sendall(payload)
        if half_close:
            s.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            data = s.recv(65536)
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks)

    yield send
    for s in opened:
        s.close()


@pytest.fixture(scope="session")
def image():
    img = os.environ.get("WISECOW_IMAGE")
    if not img:
        not_available("WISECOW_IMAGE not set (Docker runtime tests need an image)", REQUIRE_LIVE)
    if not docker_ok():
        not_available("Docker is not usable", REQUIRE_LIVE)
    return img


@pytest.fixture
def container(image, request):
    """A fresh hardened container per test, always removed afterwards."""
    cid, url = start_container(image, f"wisecow-rt-{os.getpid()}-{request.node.name[:30]}")
    try:
        yield cid, url
    finally:
        remove_container(cid)


@pytest.fixture(scope="session")
def tls_url():
    url = os.environ.get("WISECOW_TLS_URL", "").rstrip("/")
    if not url:
        not_available("WISECOW_TLS_URL not set (needs the ingress)", REQUIRE_CLUSTER)
    return url


@pytest.fixture(scope="session")
def ca_cert():
    path = os.environ.get("WISECOW_CA_CERT", "")
    if not path or not os.path.exists(path):
        not_available("WISECOW_CA_CERT not set (CA of the ingress certificate)", REQUIRE_CLUSTER)
    return path


@pytest.fixture(scope="session")
def http_ingress_url():
    url = os.environ.get("WISECOW_HTTP_INGRESS_URL", "").rstrip("/")
    if not url:
        not_available("WISECOW_HTTP_INGRESS_URL not set", REQUIRE_CLUSTER)
    return url


@pytest.fixture(scope="session")
def kubectl():
    try:
        ok = subprocess.run(KUBECTL + ["get", "ns", "wisecow"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    if not ok:
        not_available("no reachable cluster with namespace wisecow (set KUBECTL)", REQUIRE_CLUSTER)
    return KUBECTL
