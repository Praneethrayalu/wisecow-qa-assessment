"""Shared fixtures for the Wisecow QA suite.

Endpoints come from environment variables so the same suite runs against
`docker run`, a `kubectl port-forward`, or the TLS ingress:

  WISECOW_URL        plain-HTTP endpoint of the app     (default http://127.0.0.1:18081)
  WISECOW_TLS_URL    HTTPS endpoint through the ingress (optional, e.g. https://wisecow.local:8443)
  WISECOW_HTTP_INGRESS_URL  plain-HTTP ingress endpoint, used for the redirect check (optional)
  WISECOW_PACE       seconds to wait between requests (default 0.6, see DEF-01 in TEST_PLAN.md)
  KUBECTL            kubectl command for dry-run checks (default "kubectl")
"""
import os
import pathlib
import time

import pytest
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

REPO = pathlib.Path(__file__).resolve().parent.parent
BASE_URL = os.environ.get("WISECOW_URL", "http://127.0.0.1:18081").rstrip("/")
TLS_URL = os.environ.get("WISECOW_TLS_URL", "").rstrip("/")
HTTP_INGRESS_URL = os.environ.get("WISECOW_HTTP_INGRESS_URL", "").rstrip("/")
PACE = float(os.environ.get("WISECOW_PACE", "0.8"))
RETRIES = int(os.environ.get("WISECOW_RETRIES", "2"))


class PacedClient:
    """Small HTTP client that waits PACE seconds between calls.

    wisecow.sh serves one connection at a time from an `nc` loop, so a request
    sent while the loop is restarting is refused (see DEF-01). Functional tests
    pace themselves so they test behaviour, not that known race; the race has
    its own tests (TC-11, TC-12) in test_http_service.py.
    A refused connection is retried up to WISECOW_RETRIES times (default 2).
    """

    def __init__(self, base, verify=False):
        self.base, self.verify, self._last = base, verify, 0.0
        self.resets = 0

    def request(self, method, path="/", **kw):
        wait = PACE - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        kw.setdefault("timeout", 10)
        kw.setdefault("verify", self.verify)
        for attempt in range(RETRIES + 1):
            try:
                return requests.request(method, self.base + path, **kw)
            except requests.ConnectionError:
                # a reset here is DEF-01 (the nc loop was between connections);
                # functional tests retry it, TC-11/TC-12 measure it on purpose
                self.resets += 1
                if attempt == RETRIES:
                    raise
                time.sleep(1.0)
            finally:
                self._last = time.monotonic()

    def get(self, path="/", **kw):
        return self.request("GET", path, **kw)


def _reachable(url):
    try:
        requests.get(url + "/", timeout=5, verify=False)
        return True
    except requests.RequestException:
        return False


@pytest.fixture(scope="session")
def repo():
    return REPO


@pytest.fixture(scope="session")
def client():
    if not _reachable(BASE_URL):
        time.sleep(1)
        if not _reachable(BASE_URL):
            pytest.skip(f"Wisecow not reachable at {BASE_URL} (set WISECOW_URL)")
    time.sleep(PACE)
    return PacedClient(BASE_URL)


@pytest.fixture(scope="session")
def tls_client():
    if not TLS_URL:
        pytest.skip("WISECOW_TLS_URL not set")
    if not _reachable(TLS_URL):
        pytest.skip(f"TLS endpoint not reachable at {TLS_URL}")
    return PacedClient(TLS_URL)
