r"""Ingress, TLS and live-cluster tests (TC-25 .. TC-33). Optional: each test is skipped with a
reason when its endpoint is not configured (WISECOW_REQUIRE_CLUSTER=1 makes that a failure).

Local setup used for the recorded run (kind + ingress-nginx + cert-manager):
  kubectl -n ingress-nginx port-forward svc/ingress-nginx-controller 8443:443 8080:80
  kubectl -n wisecow get secret wisecow-tls -o jsonpath='{.data.ca\.crt}' | base64 -d > /tmp/wisecow-ca.crt
  WISECOW_TLS_URL=https://wisecow.local:8443 WISECOW_CA_CERT=/tmp/wisecow-ca.crt \
  WISECOW_HTTP_INGRESS_URL=http://wisecow.local:8080 KUBECTL="kubectl" pytest -m cluster
"""
import concurrent.futures as cf
import json
import socket
import ssl
from urllib.parse import urlsplit

import pytest
import requests

from conftest import TIMEOUT

pytestmark = pytest.mark.cluster


def test_https_serves_the_cow_with_verified_certificate(tls_url, ca_cert):
    """TC-25: HTTPS works with full verification (CA + hostname wisecow.local)."""
    r = requests.get(tls_url + "/", timeout=TIMEOUT, verify=ca_cert)
    assert r.status_code == 200 and "(oo)" in r.text


def test_self_signed_certificate_is_not_publicly_trusted(tls_url):
    """TC-26: with the normal public trust store the self-signed certificate is REJECTED.
    (Local TLS is for testing only; production needs a publicly trusted issuer such as Let's Encrypt.)"""
    with pytest.raises(requests.exceptions.SSLError):
        requests.get(tls_url + "/", timeout=TIMEOUT, verify=True)


def test_wrong_hostname_is_rejected(tls_url, ca_cert):
    """TC-27: hostname validation - connecting with SNI 'evil.example' does not yield a trusted cert."""
    parts = urlsplit(tls_url)
    ctx = ssl.create_default_context(cafile=ca_cert)
    with socket.create_connection((parts.hostname, parts.port or 443), timeout=TIMEOUT) as raw:
        with pytest.raises(ssl.SSLCertVerificationError):
            with ctx.wrap_socket(raw, server_hostname="evil.example"):
                pass


def test_modern_tls_negotiated(tls_url, ca_cert):
    """TC-28: TLS 1.2 or 1.3 is negotiated."""
    parts = urlsplit(tls_url)
    ctx = ssl.create_default_context(cafile=ca_cert)
    with socket.create_connection((parts.hostname, parts.port or 443), timeout=TIMEOUT) as raw:
        with ctx.wrap_socket(raw, server_hostname=parts.hostname) as s:
            assert s.version() in ("TLSv1.2", "TLSv1.3")


@pytest.mark.parametrize("version", ["TLSv1", "TLSv1_1"])
def test_obsolete_tls_rejected(tls_url, version):
    """TC-29: TLS 1.0 and 1.1 handshakes are refused."""
    parts = urlsplit(tls_url)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.minimum_version = ctx.maximum_version = getattr(ssl.TLSVersion, version)
        ctx.set_ciphers("ALL:@SECLEVEL=0")
    except (ValueError, ssl.SSLError):
        pytest.skip(f"not run: this client's OpenSSL cannot even offer {version}")
    with socket.create_connection((parts.hostname, parts.port or 443), timeout=TIMEOUT) as raw:
        with pytest.raises((ssl.SSLError, ConnectionResetError)):
            with ctx.wrap_socket(raw, server_hostname=parts.hostname):
                pass


def test_hsts_header(tls_url, ca_cert):
    """TC-30: HTTPS responses carry Strict-Transport-Security."""
    r = requests.get(tls_url + "/", timeout=TIMEOUT, verify=ca_cert)
    assert "max-age=" in r.headers.get("Strict-Transport-Security", "")


def test_http_redirects_to_https(http_ingress_url):
    """TC-31: plain HTTP is redirected (308) to https://."""
    r = requests.get(http_ingress_url + "/", timeout=TIMEOUT, allow_redirects=False)
    assert r.status_code in (301, 308) and r.headers["Location"].startswith("https://")


def test_burst_through_ingress(tls_url, ca_cert):
    """TC-32 (was DEF-03): 50 requests, 10 at a time, through ingress -> 2 replicas: all 200, no 502."""
    def one(_):
        try:
            return requests.get(tls_url + "/", timeout=30, verify=ca_cert).status_code
        except requests.RequestException as e:
            return type(e).__name__
    with cf.ThreadPoolExecutor(max_workers=10) as pool:
        codes = list(pool.map(one, range(50)))
    assert codes.count(200) == 50, sorted(set(map(str, codes)))


def test_live_deployment_healthy_and_hardened(kubectl):
    """TC-33: the live Deployment has all replicas ready and runs with the hardened securityContext."""
    import subprocess
    d = json.loads(subprocess.run(kubectl + ["-n", "wisecow", "get", "deploy", "wisecow", "-o", "json"],
                                  capture_output=True, text=True, timeout=30, check=True).stdout)
    assert d["status"].get("readyReplicas") == d["spec"]["replicas"] >= 2
    pod = d["spec"]["template"]["spec"]
    c = pod["containers"][0]["securityContext"]
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert c["readOnlyRootFilesystem"] is True and c["capabilities"]["drop"] == ["ALL"]
