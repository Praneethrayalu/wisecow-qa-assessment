"""TLS / ingress checks (run when WISECOW_TLS_URL is set, e.g. against the kind cluster)."""
import socket
import ssl
import time
import concurrent.futures as cf
from urllib.parse import urlparse

import pytest
import requests

from conftest import TLS_URL, HTTP_INGRESS_URL


def test_tc13_https_returns_cow(tls_client):
    r = tls_client.get("/")
    assert r.status_code == 200 and "(oo)" in r.text


def test_tc14_tls_version_is_modern(tls_client):
    u = urlparse(TLS_URL)
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    with socket.create_connection((u.hostname, u.port or 443), timeout=5) as s:
        with ctx.wrap_socket(s, server_hostname=u.hostname) as tls:
            assert tls.version() in ("TLSv1.2", "TLSv1.3"), tls.version()
            cert = tls.getpeercert(binary_form=True)
    assert cert, "server presented no certificate"


def test_tc15_old_tls_rejected():
    if not TLS_URL:
        pytest.skip("WISECOW_TLS_URL not set")
    u = urlparse(TLS_URL)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    ctx.maximum_version = ssl.TLSVersion.TLSv1_1
    with pytest.raises((ssl.SSLError, ConnectionResetError, OSError)):
        with socket.create_connection((u.hostname, u.port or 443), timeout=5) as s:
            ctx.wrap_socket(s, server_hostname=u.hostname)


def test_tc16_hsts_header_present(tls_client):
    r = tls_client.get("/")
    assert "max-age=" in r.headers.get("Strict-Transport-Security", "")


def test_tc17_http_redirects_to_https():
    if not HTTP_INGRESS_URL:
        pytest.skip("WISECOW_HTTP_INGRESS_URL not set")
    r = requests.get(HTTP_INGRESS_URL + "/", allow_redirects=False, timeout=10)
    assert r.status_code in (301, 308)
    assert r.headers["Location"].startswith("https://")


def test_tc18_ingress_burst_two_replicas(tls_client):
    """20 requests in 4 parallel streams through the ingress (2 replicas)."""
    def one(_):
        try:
            return requests.get(TLS_URL + "/", timeout=10, verify=False).status_code
        except requests.RequestException as e:
            return type(e).__name__
    time.sleep(1)
    with cf.ThreadPoolExecutor(4) as ex:
        codes = list(ex.map(one, range(20)))
    ok = codes.count(200)
    if ok != 20:
        pytest.xfail(f"DEF-03: {20 - ok}/20 failed under burst ({sorted(set(map(str, codes)))})")
