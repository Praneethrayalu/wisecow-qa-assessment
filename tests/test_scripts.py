"""Unit tests for the Problem Statement 2 scripts (no cluster needed)."""
import gzip
import http.server
import importlib.util
import json
import subprocess
import sys
import threading
import time

import pytest

from conftest import REPO

SCRIPTS = REPO / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


log_analyzer = load("log_analyzer")
health = load("app_health_checker")

LOG = [
    '10.0.0.1 - - [15/Oct/2026:08:00:00 +0000] "GET / HTTP/1.1" 200 500 "-" "curl/8.0"',
    '10.0.0.1 - - [15/Oct/2026:08:00:05 +0000] "GET /about HTTP/1.1" 200 300 "-" "curl/8.0"',
    '10.0.0.2 - - [15/Oct/2026:08:10:00 +0000] "GET /admin HTTP/1.1" 404 120 "-" "Mozilla/5.0"',
    '10.0.0.2 - - [15/Oct/2026:09:00:00 +0000] "GET /admin HTTP/1.1" 404 120 "-" "Mozilla/5.0"',
    '10.0.0.3 - - [15/Oct/2026:09:30:00 +0000] "POST /login HTTP/1.1" 500 - "-" "Mozilla/5.0"',
    'this line is garbage',
]


@pytest.fixture
def logfile(tmp_path):
    p = tmp_path / "access.log"
    p.write_text("\n".join(LOG) + "\n")
    return p


# ---------------- log_analyzer.py ----------------

def test_tc26_log_counts(logfile):
    c = log_analyzer.analyze([str(logfile)])
    assert c["total"] == 5 and c["unparsed"] == 1
    assert c["status"]["404"] == 2 and c["status"]["500"] == 1
    assert c["not_found_pages"].most_common(1) == [("/admin", 2)]
    assert c["ips"].most_common(1)[0][1] == 2
    assert c["bytes"] == 500 + 300 + 120 + 120          # "-" size is skipped
    assert c["methods"]["POST"] == 1
    assert len(c["hours"]) == 2


def test_tc27_log_gzip_and_multiple_files(logfile, tmp_path):
    gz = tmp_path / "access.log.1.gz"
    with gzip.open(gz, "wt") as fh:
        fh.write(LOG[0] + "\n")
    c = log_analyzer.analyze([str(logfile), str(gz)])
    assert c["total"] == 6 and c["pages"]["/"] == 2


def test_tc28_log_cli_text_and_json(logfile):
    out = subprocess.run([sys.executable, SCRIPTS / "log_analyzer.py", logfile],
                         capture_output=True, text=True, check=True).stdout
    assert "404 errors     : 2" in out and "/admin" in out
    js = json.loads(subprocess.run([sys.executable, SCRIPTS / "log_analyzer.py", logfile, "--json", "--top", "1"],
                                   capture_output=True, text=True, check=True).stdout)
    assert js["total"] == 5 and js["not_found_pages"] == {"/admin": 2}


def test_tc29_log_empty_file(tmp_path):
    p = tmp_path / "empty.log"
    p.write_text("")
    c = log_analyzer.analyze([str(p)])
    assert c["total"] == 0 and c["unparsed"] == 0


# ---------------- app_health_checker.py ----------------

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/slow":
            time.sleep(2)
        code = {"/ok": 200, "/boom": 500, "/slow": 200, "/created": 201}.get(self.path, 404)
        self.send_response(code)
        self.end_headers()
        self.wfile.write(b"hi")

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_tc30_health_up(server):
    r = health.check_once(server + "/ok", 200, 3)
    assert r["state"] == "UP" and r["status_code"] == 200 and r["latency_ms"] >= 0


def test_tc31_health_down_on_5xx_and_404(server):
    assert health.check_once(server + "/boom", 200, 3)["state"] == "DOWN"
    r = health.check_once(server + "/nope", 200, 3)
    assert r["state"] == "DOWN" and r["status_code"] == 404


def test_tc32_health_custom_expect(server):
    assert health.check_once(server + "/created", 201, 3)["state"] == "UP"
    assert health.check_once(server + "/boom", 500, 3)["state"] == "UP"


def test_tc33_health_timeout_and_refused(server):
    r = health.check_once(server + "/slow", 200, 0.5)
    assert r["state"] == "DOWN" and "timeout" in r["reason"].lower() or "timed out" in r["reason"].lower()
    r = health.check_once("http://127.0.0.1:1/", 200, 2)
    assert r["state"] == "DOWN" and r["status_code"] is None


def test_tc34_health_retries_then_gives_up(server):
    t = time.monotonic()
    r = health.check_with_retries(server + "/boom", 200, 2, retries=3, backoff=0.1)
    assert r["attempt"] == 3 and r["state"] == "DOWN"
    assert time.monotonic() - t >= 0.1 + 0.2


def test_tc35_health_cli_exit_codes(server, tmp_path):
    cmd = [sys.executable, SCRIPTS / "app_health_checker.py", "--retries", "1", "--log", tmp_path / "h.log"]
    up = subprocess.run(cmd + [server + "/ok"], capture_output=True, text=True)
    down = subprocess.run(cmd + [server + "/ok", server + "/boom"], capture_output=True, text=True)
    assert up.returncode == 0 and "UP" in up.stdout
    assert down.returncode == 1 and "DOWN" in down.stdout
    assert "DOWN" in (tmp_path / "h.log").read_text()
