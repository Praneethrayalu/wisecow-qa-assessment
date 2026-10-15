# Wisecow on Kubernetes, with a QA test suite - AccuKnox QA Trainee assessment

Candidate: **Repalle Praneeth Rayalu** · Praneeth1146a@gmail.com · B.Tech CSE (Cyber Security), LPU 2026

Upstream app: [nyrahul/wisecow](https://github.com/nyrahul/wisecow) (see `UPSTREAM.md`).

## 1. Problem statement and objectives

> Source: the problem-statement PDF linked from the AccuKnox QA Trainee form (PS1 + PS2). The form also
> asks for a test-case document / Google Sheet; the test plan is `tests/TEST_PLAN.md` and the same cases are
> in the Google Sheet linked from the form answers. KubeArmor (PS3) is not in the QA PDF; the policy is
> included as an extra.

- **PS1** - containerise the Wisecow app (Dockerfile), deploy it on Kubernetes (Deployment + Service),
  expose it, build and push the image with GitHub Actions, *challenge:* continuous deployment and TLS.
- **PS2** - any two of four ops scripts. I wrote three: system health monitor (Bash), application health
  checker (Python) and log analyzer (Python).
- **QA layer (my focus for this role)** - a layered pytest suite (unit, static, live HTTP, Docker runtime,
  ingress/TLS), a test plan with traceable test cases and a defect log, and CI gates that block the
  image push when tests fail.
- **Extra** - a zero-trust KubeArmor policy (not required by the QA PDF).

## 2. Architecture and design decisions

```
client --HTTPS--> ingress-nginx (TLS, HTTP->HTTPS 308) --> Service wisecow (ClusterIP :80)
       --> Deployment wisecow (2 pods, :4499) --> socat --fork--> wisecow.sh --handle --> fortune | cowsay
```

| Decision | Why |
|---|---|
| `socat ... fork` + a Bash handler per connection | The original `nc -l` loop accepted **one** connection, exited and restarted; requests arriving in that gap were reset (and 502s through the ingress). socat keeps listening and forks a short-lived handler per request, so back-to-back and parallel requests work. Stays a small shell app, no web framework. `max-children` caps parallel handlers (default 32). |
| Proper HTTP in the handler | Status line + `Content-Type` + exact `Content-Length` + `Connection: close`; 400 (malformed), 404 (path), 405 + `Allow` (method), 408 (silent client, 5 s), 413 (body > 1 MiB), 431 (> 100 headers), 500 (fortune/cowsay failed). Output is HTML-escaped. |
| Debian slim, pinned by digest | `fortune-mod`, `cowsay` and `socat` are packaged there; the digest makes builds start from the same bytes. |
| Non-root, read-only | Runs as uid 10001; app file is root-owned 0555; `readOnlyRootFilesystem: true` works because the new app writes nothing (the old one needed a FIFO). |
| HTTP probes | `startupProbe`, `readinessProbe`, `livenessProbe` all do a real `GET /` (which runs fortune + cowsay), not just a TCP connect. |
| ClusterIP + Ingress | ClusterIP is internal only; the Ingress is the single public entry point and terminates TLS. A NodePort (plain HTTP, bypasses TLS) is kept only as an optional debug file in `k8s/optional/`. |
| cert-manager | Self-signed `ClusterIssuer` for local testing; `k8s/tls/letsencrypt-issuer.example.yaml` for a real domain. A self-signed certificate is **not** publicly trusted - clients must be given its CA (tests prove the public trust store rejects it). |

## 3. Project structure

```
wisecow.sh                      the app (listener + per-connection HTTP handler)
Dockerfile, .dockerignore, .hadolint.yaml
k8s/  namespace, deployment, service, ingress, kustomization
k8s/tls/  cert-manager issuer + certificate, Let's Encrypt example, openssl fallback script
k8s/optional/nodeport-service.yaml   debug only, not deployed by kustomize/CI
k8s/schemas/kubearmorpolicy_v1.json  JSON schema generated from the KubeArmor CRD (for kubeconform)
kubearmor/  zero-trust allow-list policy + namespace "block" posture (PS3)
scripts/  system_health_monitor.sh, app_health_checker.py, log_analyzer.py (PS2)
tests/  pytest suite (conftest.py fixtures, test_*.py), TEST_PLAN.md (plan, cases, defect log),
        smoke_test.sh, requirements.txt (pinned); pytest.ini (markers, JUnit, timeouts)
.github/workflows/ci-cd.yaml
docs/evidence/   outputs of the verification runs (current code)
docs/baseline/   outputs from the first version, kept for comparison
```

## 4. Prerequisites

Docker 24+, kubectl 1.29+, a cluster (kind/minikube/real) with ingress-nginx and cert-manager, Python 3.9+
(standard library only for the scripts; `pytest` for the tests), curl. Tested with kind v0.27 /
Kubernetes v1.32, ingress-nginx, cert-manager, Python 3.11.

## 5. Install and run

```bash
# local container
docker build -t wisecow:local .
docker run -d --name wisecow --read-only --cap-drop ALL --security-opt no-new-privileges -p 4499:4499 wisecow:local
curl -i http://localhost:4499/
tests/smoke_test.sh http://localhost:4499

# Kubernetes (kind example)
kind create cluster --name wisecow
kind load docker-image wisecow:local --name wisecow
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.16.2/cert-manager.yaml
kubectl apply -k k8s
kubectl -n wisecow set image deployment/wisecow wisecow=wisecow:local
kubectl apply -f k8s/tls/cert-manager.yaml           # or: k8s/tls/make-selfsigned-secret.sh
kubectl -n wisecow rollout status deployment/wisecow
echo "127.0.0.1 wisecow.local" | sudo tee -a /etc/hosts
kubectl -n ingress-nginx port-forward svc/ingress-nginx-controller 8443:443 8080:80 &
kubectl -n wisecow get secret wisecow-tls -o jsonpath='{.data.ca\.crt}' | base64 -d > wisecow-ca.crt
curl --cacert wisecow-ca.crt https://wisecow.local:8443/
```

Rollback: `kubectl -n wisecow rollout undo deployment/wisecow` (3 old ReplicaSets are kept; the CI deploy
does this automatically if a rollout does not finish in 180 s).

PS2 scripts:

```bash
bash scripts/system_health_monitor.sh -c 80 -m 80 -d 85 -p 400        # -j for JSON, -i 60 to repeat
python3 scripts/app_health_checker.py https://wisecow.local:8443/ --cacert wisecow-ca.crt --retries 3
python3 scripts/log_analyzer.py docs/sample_access.log --top 5          # --json for JSON
```
Exit codes: 0 = healthy / report produced, 1 = alert / DOWN / nothing parseable, 2 = bad arguments or
unreadable file.

## 6. Environment variables and secrets

| Name | Where | Meaning |
|---|---|---|
| `SRVPORT` | container | listen port (default 4499) |
| `MAX_CONN` | container | max parallel request handlers (default 32) |
| `READ_TIMEOUT` | container | seconds to wait for a request before 408 (default 5) |
| `GITHUB_TOKEN` | Actions (automatic) | pushes to GHCR; only the `push` job gets `packages: write` |
| `KUBE_CONFIG` | Actions **secret** | base64 kubeconfig for a service account limited to the `wisecow` namespace, e.g. `base64 -w0 kubeconfig` |
| `DEPLOY_ENABLED` | Actions **variable** | set to `true` to enable the deploy job; otherwise it shows as *skipped* |

No secret is committed to this repository.

## 7. Tests and actual results

Full plan, all 69 test cases and the defect log: **[`tests/TEST_PLAN.md`](tests/TEST_PLAN.md)**.

| Run (15 Oct 2026, fresh venv, pinned deps) | Passed | Failed | Skipped | xfail | Evidence |
|---|---|---|---|---|---|
| Full suite: unit + static + live + Docker + ingress/TLS (live and cluster *required*) | **101** | 0 | 0 | 0 | `docs/evidence/03_pytest_full_suite.txt`, `junit-full.xml` |
| CI split `-m "unit or static"` | 57 | 0 | 0 | 0 | `04_pytest_ci_split_static_unit.txt` |
| New live tests against the **original** image | 1 | 29 | 0 | 0 | `05_new_live_tests_vs_ORIGINAL_image.txt` (defects reproduce) |
| Old suite on the original app (baseline) | 31 | 0 | 0 | 4 | `docs/baseline/outputs/12_old_suite_rerun_before_fixes.txt` |

The three defects that were xfail before (DEF-01 resets, DEF-02 missing headers, DEF-03 502s under a burst)
are fixed in `wisecow.sh` and their tests are now strict passes. Static checks (shellcheck, hadolint,
actionlint, bandit, kubeconform incl. KubeArmor schema, server-side dry run) are clean:
`docs/evidence/01_static_checks.txt`, `02_server_side_dry_run.txt`. Live ingress/TLS checks:
`08_ingress_tls_burst.txt` (100/100 through the ingress).

Run it yourself:
```bash
python -m venv .venv && . .venv/bin/activate && pip install -r tests/requirements.txt
pytest -m "unit or static"
docker build -t wisecow:local . && WISECOW_IMAGE=wisecow:local pytest -m "live or docker" --junitxml=report.xml
```
Environment variables for the suite are listed at the top of `tests/conftest.py`.

The GitHub Actions workflow has **not** run on GitHub yet (repo not pushed when this was written).

## 8. CI/CD (GitHub Actions)

`static-and-unit` (shellcheck, py_compile, `pytest -m "unit or static"` with kubeconform, JUnit artifact) →
`qa-tests` (build the image, `pytest -m "live or docker"` with `WISECOW_REQUIRE_LIVE=1`, JUnit artifact) → `push` (main only; pushes **the tested image** to
`ghcr.io/<owner>/wisecow-qa` as `sha-<commit>` and `latest`, records the digest) → `deploy` (only when `DEPLOY_ENABLED=true`; checks
`KUBE_CONFIG`, `kubectl apply -k k8s`, `set image ...@sha256:<digest>`, waits for rollout, rolls back on
failure, verifies with the health checker and the smoke test). Pull requests run the two test jobs only. Ingress/TLS tests need a cluster and are run against one
(kind here), not in CI.
Default token permission is `contents: read`.

## 9. Known limitations

- Load testing is limited to small bursts (50-100 requests); no performance benchmark is claimed.
- The workflow has not been executed on GitHub; deployment has been verified only on a local kind cluster,
  not on a cloud cluster with a public domain.
- KubeArmor policy: syntax validated (schema + server dry run) and applied, but **enforcement is unverified**:
  the test machine's kernel had no BPF-LSM/AppArmor, so KubeArmor ran in visibility-only mode.
- Each request forks bash + fortune + perl (cowsay): fine for this app, not for high traffic.
- Base image digest and apt packages need periodic refresh for security fixes (DL3008 ignored, reason in `.hadolint.yaml`).

## 10. Security considerations

Non-root uid 10001, `runAsNonRoot`, `allowPrivilegeEscalation: false`, all capabilities dropped,
seccomp `RuntimeDefault`, read-only root filesystem, no service-account token, resource limits, TLS at
the ingress with forced redirect and HSTS, least-privilege workflow token, kubeconfig only from a secret
via env (never echoed or inlined), deploys by immutable digest, input limits in the handler (header count,
body size, read timeout, concurrency cap), HTML-escaped output.

## 11. Troubleshooting

| Symptom | Fix |
|---|---|
| `ImagePullBackOff` on kind | `kind load docker-image <image> --name wisecow`, then `kubectl set image` to that tag |
| `curl: (60)` over HTTPS | expected with a self-signed cert: pass `--cacert wisecow-ca.crt` (do not use `-k` in automation) |
| Ingress 404 | `wisecow.local` must resolve to the ingress (`/etc/hosts`) and the Host header must match |
| Certificate not Ready | `kubectl describe certificate -n wisecow wisecow-tls`; is cert-manager running? |
| Deploy job skipped | set the repository variable `DEPLOY_ENABLED=true` and the `KUBE_CONFIG` secret |
| `docker build` fails on `COPY --chmod` | not used any more; plain `docker build` works without BuildKit |

## 12. Evidence of verification

All outputs are in `docs/evidence/` (each file starts with the exact command and ends with its exit code).
`docs/baseline/` holds the outputs of the first version for comparison. `INTERVIEW_PREP.md` explains the
design in interview form.
