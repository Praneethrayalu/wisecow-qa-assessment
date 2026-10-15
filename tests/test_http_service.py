"""Black-box HTTP tests against a running wisecow (TC-01 .. TC-16).

TC-11/TC-12 reproduced DEF-01 (nc loop: one connection at a time) and TC-03 DEF-02 (no
Content-Type) on the original wisecow.sh. They were xfail before the fix; they are normal,
strict tests now, so a regression turns the run red.
"""
import concurrent.futures as cf
import socket
import statistics
import time

import pytest
import requests

from conftest import TIMEOUT

pytestmark = pytest.mark.live


def parse(raw: bytes):
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers = {}
    for line in lines[1:]:
        k, _, v = line.partition(":")
        headers[k.strip().lower()] = v.strip()
    return lines[0], headers, body


def test_get_root_returns_200(base_url):
    """TC-01: GET / answers 200."""
    assert requests.get(base_url + "/", timeout=TIMEOUT).status_code == 200


def test_body_is_a_cow_in_pre(base_url):
    """TC-02: the body is cowsay art inside <pre>."""
    body = requests.get(base_url + "/", timeout=TIMEOUT).text
    assert body.startswith("<pre>") and body.rstrip().endswith("</pre>")
    assert "(oo)" in body and "^__^" in body


def test_headers_content_type_and_length(raw_http):
    """TC-03 (was DEF-02): valid status line, Content-Type and an exact Content-Length."""
    status, headers, body = parse(raw_http(b"GET / HTTP/1.1\r\nHost: t\r\n\r\n"))
    assert status == "HTTP/1.1 200 OK"
    assert headers["content-type"].startswith("text/html")
    assert int(headers["content-length"]) == len(body)
    assert headers["connection"] == "close"
    assert headers["x-content-type-options"] == "nosniff"


def test_head_returns_headers_without_body(raw_http):
    """TC-04: HEAD / is 200 with headers and no body."""
    status, headers, body = parse(raw_http(b"HEAD / HTTP/1.1\r\nHost: t\r\n\r\n"))
    assert status.startswith("HTTP/1.1 200") and int(headers["content-length"]) > 0 and body == b""


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
def test_unsupported_methods_get_405(base_url, method):
    """TC-05: methods other than GET/HEAD get 405 with an Allow header."""
    r = requests.request(method, base_url + "/", data=b"x=1", timeout=TIMEOUT)
    assert r.status_code == 405 and r.headers["Allow"] == "GET, HEAD"


@pytest.mark.parametrize("path,code", [("/", 200), ("/?name=cow", 200), ("/missing", 404),
                                       ("/index.html", 404), ("/../etc/passwd", 404)])
def test_paths(base_url, raw_http, path, code):
    """TC-06: only / serves the cow (a query string is ignored); other paths are 404."""
    status, _, _ = parse(raw_http(f"GET {path} HTTP/1.1\r\nHost: t\r\n\r\n".encode()))
    assert status.split()[1] == str(code)


@pytest.mark.parametrize("payload", [b"garbage\r\n\r\n", b"GET /\r\n\r\n", b"GET / HTTP/9.9\r\n\r\n",
                                     b"get / HTTP/1.1\r\n\r\n", b"GET noslash HTTP/1.1\r\n\r\n",
                                     b"GET / HTTP/1.1 extra\r\n\r\n", b"\r\n\r\n"])
def test_malformed_request_line_gets_400(raw_http, payload):
    """TC-07: a malformed request line gets a clean 400, not a hang or a reset."""
    status, _, _ = parse(raw_http(payload))
    assert status.startswith("HTTP/1.1 400")


def test_too_many_headers_gets_431(raw_http):
    """TC-08: more than 100 header lines are refused with 431."""
    hdrs = b"".join(b"X-%d: y\r\n" % i for i in range(150))
    status, _, _ = parse(raw_http(b"GET / HTTP/1.1\r\n" + hdrs + b"\r\n"))
    assert status.startswith("HTTP/1.1 431")


def test_silent_client_gets_408(host_port):
    """TC-09: a client that connects and sends nothing is answered 408 after the read timeout (5 s)."""
    with socket.create_connection(host_port, timeout=15) as s:
        started = time.monotonic()
        data = s.recv(200)
        elapsed = time.monotonic() - started
    assert data.startswith(b"HTTP/1.1 408") and 3 <= elapsed <= 12


def test_abrupt_disconnects_do_not_break_the_server(host_port, base_url):
    """TC-10: clients that vanish mid-request (with and without a partial request) leave the server healthy."""
    for i in range(10):
        s = socket.create_connection(host_port, timeout=5)
        if i % 2:
            s.sendall(b"GET / HT")
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")  # RST on close
        s.close()
    assert requests.get(base_url + "/", timeout=TIMEOUT).status_code == 200


def test_back_to_back_requests_all_succeed(base_url):
    """TC-11 (was DEF-01): 30 sequential requests with no pause all get 200."""
    codes = []
    for _ in range(30):
        try:
            codes.append(requests.get(base_url + "/", timeout=TIMEOUT).status_code)
        except requests.ConnectionError:
            codes.append("reset")
    assert codes == [200] * 30, codes


def test_concurrent_requests_all_succeed(base_url):
    """TC-12 (was DEF-01): 30 parallel requests all get 200."""
    def one(_):
        try:
            return requests.get(base_url + "/", timeout=30).status_code
        except requests.ConnectionError:
            return "reset"
    with cf.ThreadPoolExecutor(max_workers=30) as pool:
        codes = list(pool.map(one, range(30)))
    assert codes.count(200) == 30, codes


def test_keep_alive_client_is_served(base_url):
    """TC-13: a client that would reuse connections (requests.Session) still gets correct answers."""
    with requests.Session() as s:
        codes = [s.get(base_url + "/", timeout=TIMEOUT).status_code for _ in range(10)]
    assert codes == [200] * 10


def test_fortunes_vary(base_url):
    """TC-14: the wisdom is random - 10 requests give at least 2 different bodies."""
    bodies = {requests.get(base_url + "/", timeout=TIMEOUT).text for _ in range(10)}
    assert len(bodies) >= 2


def test_latency_is_reasonable(base_url):
    """TC-15: sanity bound, not a benchmark - median under 1 s and worst under 3 s over 10 requests."""
    times = []
    for _ in range(10):
        started = time.monotonic()
        requests.get(base_url + "/", timeout=TIMEOUT)
        times.append(time.monotonic() - started)
    assert statistics.median(times) < 1.0 and max(times) < 3.0, times


def test_unreachable_service_is_reported_down():
    """TC-16: the health checker reports a closed port as DOWN (service unavailability)."""
    import sys
    from conftest import REPO
    sys.path.insert(0, str(REPO / "scripts"))
    import app_health_checker as ahc
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    r = ahc.check_once(f"http://127.0.0.1:{port}/", 200, 2)
    assert r["state"] == "DOWN"
