"""Unit tests for the request handler in wisecow.sh (TC-17 .. TC-20).

`wisecow.sh --handle` reads one request on stdin and writes one response on stdout, so it can be
tested without a network or Docker. fortune and cowsay are replaced by stubs on PATH, which makes
the output deterministic (including a fortune with HTML special characters and a failing fortune).
"""
import os
import pathlib
import shutil
import subprocess

import pytest

from conftest import REPO

pytestmark = pytest.mark.unit
APP = str(REPO / "wisecow.sh")


@pytest.fixture
def stubs(tmp_path):
    def make(fortune_text="Hello <b>&</b> world", fortune_rc=0):
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        (bindir / "fortune").write_text(f"#!/bin/sh\nprintf '%s\\n' '{fortune_text}'\nexit {fortune_rc}\n")
        (bindir / "cowsay").write_text("#!/bin/sh\necho ' ___'\ncat\necho '  (oo)'\n")
        for f in bindir.iterdir():
            f.chmod(0o755)
        return {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "READ_TIMEOUT": "2"}
    return make


def handle(env, request: bytes):
    p = subprocess.run(["bash", APP, "--handle"], input=request, env=env, capture_output=True, timeout=20)
    head, _, body = p.stdout.partition(b"\r\n\r\n")
    return head.decode().split("\r\n"), body


@pytest.mark.skipif(not shutil.which("bash"), reason="bash not installed")
def test_handler_escapes_html_and_sets_exact_length(stubs):
    """TC-17: fortune text with <, >, & is HTML-escaped; Content-Length equals the body size."""
    head, body = handle(stubs(), b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
    assert head[0] == "HTTP/1.1 200 OK"
    assert b"&lt;b&gt;&amp;&lt;/b&gt;" in body and b"<b>" not in body
    length = [h for h in head if h.lower().startswith("content-length")][0].split(":")[1]
    assert int(length) == len(body)


def test_handler_returns_500_when_fortune_fails(stubs):
    """TC-18: if fortune/cowsay fail, the client gets a 500 instead of an empty 200."""
    head, _ = handle(stubs(fortune_rc=1), b"GET / HTTP/1.1\r\n\r\n")
    assert head[0] == "HTTP/1.1 500 Internal Server Error"


def test_handler_discards_request_body(stubs):
    """TC-19: a POST body is read and discarded, then 405 is sent (no reset of the client)."""
    head, _ = handle(stubs(), b"POST / HTTP/1.1\r\nContent-Length: 5\r\n\r\nhello")
    assert head[0].startswith("HTTP/1.1 405") and "Allow: GET, HEAD" in head


def test_handler_rejects_huge_body(stubs):
    """TC-20: a declared body over 1 MiB is refused with 413."""
    head, _ = handle(stubs(), b"POST / HTTP/1.1\r\nContent-Length: 99999999\r\n\r\n")
    assert head[0].startswith("HTTP/1.1 413")


def test_handler_missing_prerequisite_is_reported(tmp_path):
    """TC-20b: starting the server without cowsay/fortune/socat fails fast with a clear message."""
    env = {"PATH": "/nonexistent", "HOME": str(tmp_path)}
    p = subprocess.run([shutil.which("bash"), APP], env=env, capture_output=True, text=True, timeout=20)
    assert p.returncode == 1 and "Install prerequisites" in p.stderr
