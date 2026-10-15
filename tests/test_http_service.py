"""Functional + non-functional tests against the running Wisecow service (black box)."""
import concurrent.futures as cf
import re
import statistics
import time

import pytest
import requests

from conftest import BASE_URL

COW = re.compile(r"\\\s+\^__\^.*\(oo\)", re.S)   # the cow's ears and eyes


@pytest.mark.smoke
def test_tc01_root_returns_200(client):
    r = client.get("/")
    assert r.status_code == 200


@pytest.mark.smoke
def test_tc02_body_is_wrapped_in_pre(client):
    r = client.get("/")
    text = r.text.strip()
    assert text.startswith("<pre>") and text.endswith("</pre>"), text[:80]


@pytest.mark.smoke
def test_tc03_body_contains_cowsay_art(client):
    r = client.get("/")
    assert COW.search(r.text), "cow ASCII art not found"
    assert "||----w |" in r.text


def test_tc04_speech_bubble_is_not_empty(client):
    r = client.get("/")
    inner = r.text.split("<pre>", 1)[1]
    bubble = inner.split("\\   ^__^", 1)[0]
    words = re.findall(r"[A-Za-z]{2,}", bubble)
    assert len(words) >= 1, "fortune text missing from the bubble"


def test_tc05_fortunes_vary_between_requests(client):
    bodies = {client.get("/").text for _ in range(5)}
    assert len(bodies) >= 3, f"only {len(bodies)} distinct fortunes in 5 requests"


def test_tc06_any_path_is_served(client):
    for path in ("/index.html", "/does-not-exist", "/?q=1"):
        assert client.get(path).status_code == 200


def test_tc07_head_and_post_answer(client):
    assert client.request("HEAD", "/").status_code == 200
    assert client.request("POST", "/", data="hello").status_code == 200


def test_tc08_latency_under_budget(client):
    samples = []
    for _ in range(5):
        r = client.get("/")
        assert r.status_code == 200
        samples.append(r.elapsed.total_seconds())    # time to response headers, excludes pacing
    assert max(samples) < 2.0, samples
    assert statistics.median(samples) < 0.5, samples


def test_tc09_paced_sequential_requests_all_succeed(client):
    codes = [client.get("/").status_code for _ in range(8)]
    assert codes == [200] * 8


def test_tc10_response_carries_content_type(client):
    """DEF-02: the raw response is just 'HTTP/1.1 200' + body, no Content-Type."""
    r = client.get("/")
    if "Content-Type" not in r.headers:
        pytest.xfail("DEF-02: no Content-Type header (browser has to sniff the body)")
    assert r.headers["Content-Type"].startswith("text/html")


def _try_get(url):
    try:
        return requests.get(url, timeout=10, verify=False).status_code
    except requests.RequestException as e:
        return type(e).__name__


def test_tc11_back_to_back_requests_without_pause():
    """DEF-01: nc serves one connection per loop; an immediate second request is refused."""
    time.sleep(1)
    codes = [_try_get(BASE_URL + "/") for _ in range(6)]
    if codes != [200] * 6:
        pytest.xfail(f"DEF-01 reproduced: {codes}")


def test_tc12_concurrent_requests_direct():
    """DEF-01 under load: 5 parallel clients straight at one container."""
    time.sleep(1)
    with cf.ThreadPoolExecutor(5) as ex:
        codes = list(ex.map(lambda _: _try_get(BASE_URL + "/"), range(5)))
    if codes != [200] * 5:
        pytest.xfail(f"DEF-01 under concurrency: {codes}")
