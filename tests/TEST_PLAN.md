# Wisecow — Test Plan and Test Cases

Author: Repalle Praneeth Rayalu · Assessment: AccuKnox QA Trainee (Problem Statements 1 and 2) · Executed 15 Oct 2026

## 1. Scope

System under test: the Wisecow app (`wisecow.sh`, fortune + cowsay served over `nc`) as containerised by this repo's `Dockerfile` and deployed by `k8s/` (2-replica Deployment, ClusterIP + NodePort Service, nginx Ingress with cert-manager TLS), the CI/CD workflow, and the two Problem Statement 2 scripts (`scripts/app_health_checker.py`, `scripts/log_analyzer.py`).

Out of scope: the content of the fortunes, cowsay itself, the GitHub-hosted registry and a production cluster (the CD job is exercised by static checks only).

## 2. Approach

- **Black-box HTTP tests** (pytest + requests) against whatever URL is in `WISECOW_URL`: a local `docker run`, a `kubectl port-forward`, or CI's container.
- **TLS / ingress tests** against `WISECOW_TLS_URL` (the kind cluster's ingress), skipped when it is not set.
- **Static tests** of the Dockerfile, manifests and workflow (PyYAML, `kubectl apply --dry-run=client -k k8s`), plus actionlint, ShellCheck and kubeconform.
- **Unit tests** of both scripts against fixtures and a throw-away local `http.server`.
- Known defects are kept as `xfail` tests: the suite stays green, but each defect is reproduced and reported on every run, and the test turns into a pass when the bug is fixed.

## 3. Environment

Debian 12 box, Docker, kind v0.27 (Kubernetes v1.32.2) with ingress-nginx and cert-manager, Python 3.11, pytest 9.1. Endpoints used: `WISECOW_URL=http://127.0.0.1:18081` (docker run of the image), `WISECOW_TLS_URL=https://wisecow.local:8443`, `WISECOW_HTTP_INGRESS_URL=http://wisecow.local:8080` (port-forwards of the ingress controller).

## 4. Entry and exit criteria

Entry: image builds, pods Ready, ingress certificate Ready. Exit: every test case executed, all non-defect cases pass, every failure logged as a defect with steps to reproduce.

## 5. Test cases

| ID | Area | Test case | Steps | Expected | Actual | Status |
|---|---|---|---|---|---|---|
| TC-01 | Functional / Smoke | GET / returns HTTP 200 | Send GET / to WISECOW_URL | Status 200 | As expected | Pass |
| TC-02 | Functional / Smoke | Body wrapped in <pre> | GET /; inspect body | Body starts with <pre> and ends with </pre> | As expected | Pass |
| TC-03 | Functional / Smoke | Body contains cowsay art | GET /; search for the cow (^__^ ears, (oo) eyes, legs) | Cow ASCII art present | As expected | Pass |
| TC-04 | Functional | Speech bubble carries a fortune | GET /; extract text above the cow | At least one word of fortune text | As expected | Pass |
| TC-05 | Functional | Fortunes vary between requests | Send 5 paced GET / requests; compare bodies | At least 3 distinct bodies | As expected | Pass |
| TC-06 | Functional / Negative | Unknown paths and query strings | GET /index.html, /does-not-exist, /?q=1 | App answers 200 for each (single-route app) | As expected | Pass |
| TC-07 | Functional | HEAD and POST methods | HEAD / and POST / with a body | Both answer 200, no hang | As expected | Pass |
| TC-08 | Performance | Latency budget | 5 paced GET /; measure time to headers | Max < 2 s, median < 0.5 s | As expected | Pass |
| TC-09 | Reliability | Paced sequential requests | 8 GET / with 0.6 s gaps | All 8 return 200 | As expected | Pass |
| TC-10 | Standards | Content-Type header | GET /; read headers | Content-Type: text/html | DEF-02: no Content-Type header | Fail (known defect) |
| TC-11 | Reliability | Back-to-back requests, no pause | 6 GET / with no gap | All 6 return 200 | DEF-01: connection reset on 5 request(s) | Fail (known defect) |
| TC-12 | Concurrency | 5 parallel clients to one container | 5 simultaneous GET / | All 5 return 200 | DEF-01: connection reset on 3 request(s) | Fail (known defect) |
| TC-13 | TLS / Ingress | HTTPS through the ingress | GET https://wisecow.local/ | 200 and the cow in the body | As expected | Pass |
| TC-14 | TLS / Security | Negotiated TLS version | TLS handshake to the ingress | TLS 1.2 or 1.3; certificate presented | As expected | Pass |
| TC-15 | TLS / Security | Legacy TLS rejected | Handshake capped at TLS 1.1 | Handshake refused | As expected | Pass |
| TC-16 | TLS / Security | HSTS header | GET over HTTPS; read headers | Strict-Transport-Security with max-age | As expected | Pass |
| TC-17 | TLS / Ingress | HTTP redirects to HTTPS | GET http://wisecow.local/ without following redirects | 301/308 to an https:// Location | As expected | Pass |
| TC-18 | Load | Burst through ingress (2 replicas) | 20 GET in 4 parallel streams over HTTPS | All 20 return 200 | DEF-03: 8/20 failed under burst | Fail (known defect) |
| TC-19 | Static / Docker | Dockerfile hardening | Parse Dockerfile | Pinned base, non-root USER, HEALTHCHECK, EXPOSE 4499, deps installed | As expected | Pass |
| TC-20 | Static / K8s | Manifests parse, kinds present | yaml.safe_load_all on k8s/*.yaml | Namespace, Deployment, Service, Ingress present | As expected | Pass |
| TC-21 | Static / K8s | Deployment is production-shaped | Inspect Deployment spec | ≥2 replicas, probes, limits, runAsNonRoot, no priv-esc, drop ALL | As expected | Pass |
| TC-22 | Static / K8s | Service selector matches pods | Compare Service selectors with pod labels | Every Service selects the pods; targetPort http | As expected | Pass |
| TC-23 | Static / TLS | Ingress TLS wired to Certificate | Compare Ingress tls secret/hosts with cert-manager Certificate | Same secret; hosts ⊆ dnsNames; backend Service exists | As expected | Pass |
| TC-24 | Static / CI | Workflow builds, pushes, deploys | Parse ci-cd.yaml | push trigger, login + build-push, deploy needs build, rollout status, qa-tests job | As expected | Pass |
| TC-25 | Static / K8s | kubectl client dry-run | kubectl apply --dry-run=client -k k8s | All 4 objects accepted | As expected | Pass |
| TC-26 | Unit / log_analyzer | Counts from a known log | analyze() on a 6-line fixture | total 5, unparsed 1, 404×2, 500×1, bytes 1040, 2 hours | As expected | Pass |
| TC-27 | Unit / log_analyzer | Gzip + multiple files | analyze() on .log + .gz | Totals add across files | As expected | Pass |
| TC-28 | Unit / log_analyzer | CLI text and JSON output | Run script with and without --json | Report shows 404s; JSON has totals and top-N | As expected | Pass |
| TC-29 | Unit / log_analyzer | Empty log file | analyze() on an empty file | Zero totals, no crash | As expected | Pass |
| TC-30 | Unit / health checker | UP on 200 | check_once() against local http.server /ok | state UP, status 200, latency recorded | As expected | Pass |
| TC-31 | Unit / health checker | DOWN on 5xx and 404 | check_once() on /boom and /nope | state DOWN with the status code | As expected | Pass |
| TC-32 | Unit / health checker | Custom expected status | --expect 201 / 500 | UP when status equals the expectation | As expected | Pass |
| TC-33 | Unit / health checker | Timeout and connection refused | 2 s handler with 0.5 s timeout; port 1 | DOWN with timeout reason; DOWN with no status | As expected | Pass |
| TC-34 | Unit / health checker | Retries with backoff | check_with_retries(retries=3) on /boom | 3 attempts, backoff respected, DOWN | As expected | Pass |
| TC-35 | Unit / health checker | CLI exit codes + log file | Run CLI on healthy and mixed URLs | Exit 0 when all UP, 1 when any DOWN; log written | As expected | Pass |

**Result:** 35 executed · 31 passed · 4 failed on known defects · 0 skipped · 0 unexpected failures. Raw output: `docs/outputs/11_pytest.txt`.

## 6. Defects found

| ID | Severity | Summary | Details | Found by | Suggested fix |
|---|---|---|---|---|---|
| DEF-01 | High | Requests refused unless spaced out | wisecow.sh serves one connection per `nc` loop; while the loop restarts (fortune + cowsay) nothing listens, so a back-to-back or parallel request gets a connection reset. | TC-11, TC-12 | Serve with a concurrent server (socat fork / a tiny Python or Go server), or run more replicas behind the Service. |
| DEF-02 | Low | No Content-Type (or reason phrase) in the response | The response is only 'HTTP/1.1 200' plus the body; clients must sniff the type. | TC-10 | Send 'HTTP/1.1 200 OK' and 'Content-Type: text/html; charset=utf-8' headers. |
| DEF-03 | High | 502s through the ingress under a burst | Consequence of DEF-01: with 20 requests in 4 parallel streams over HTTPS, 8 to 11 of the 20 got 502 Bad Gateway from ingress-nginx across runs, on the 2-replica deployment. | TC-18 | Fix DEF-01; add replicas/HPA; readiness that reflects real capacity. |

Also found and fixed during testing (in this repo): the workflow had an unquoted `name: Image metadata (tags: sha, latest)` that made the YAML invalid, ShellCheck failed on an unused variable in `system_health_monitor.sh`, and the deploy step used the mixed-case GitHub owner in the image reference (registry paths must be lowercase).

## 7. How to run

```bash
pip install -r tests/requirements.txt
docker build -t wisecow:qa . && docker run -d -p 18081:4499 wisecow:qa
WISECOW_URL=http://127.0.0.1:18081 python -m pytest -v
# with the cluster's ingress as well:
WISECOW_TLS_URL=https://wisecow.local:8443 WISECOW_HTTP_INGRESS_URL=http://wisecow.local:8080 KUBECTL=kubectl python -m pytest -v
```
