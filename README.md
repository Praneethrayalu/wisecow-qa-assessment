# Wisecow on Kubernetes — AccuKnox QA Trainee assessment

Candidate: **Repalle Praneeth Rayalu** · Praneeth1146a@gmail.com · github.com/Praneethrayalu

Wisecow is a tiny bash web server (`fortune | cowsay` over HTTP on port 4499). This repo answers both problem
statements of the QA Trainee assessment and then **tests the result like a QA engineer would**:

- **Problem Statement 1** — Dockerfile, Kubernetes manifests (Deployment, Service, TLS Ingress), GitHub Actions CI/CD
  with continuous deployment, TLS via cert-manager.
- **Problem Statement 2** — `scripts/app_health_checker.py` (objective 4) and `scripts/system_health_monitor.sh`
  (objective 1), plus `scripts/log_analyzer.py` (objective 3) as a third.
- **QA** — a 35-case pytest suite (`tests/`), a written test plan with results and a defect log
  (`tests/TEST_PLAN.md`), a `qa-tests` job that gates the image build in CI, and the captured run in `docs/outputs/11_pytest.txt`.
- Test cases as a Google Sheet: https://docs.google.com/spreadsheets/d/1kYAg1k0UFK-3StcLwAk_7mvQJ9WxKk_0BQ-LBrxk2oM/edit?usp=sharing

## Test results (15 Oct 2026, kind cluster + docker)

**35 test cases: 31 passed, 4 failed on 3 real defects** that the suite reproduces on every run (kept as `xfail`, so CI stays
green while the defects stay visible, and each test starts passing the day its bug is fixed).

| Defect | Severity | What happens |
|---|---|---|
| DEF-01 | High | `wisecow.sh` serves one connection per `nc` loop. A request sent while the loop restarts gets a connection reset, so back-to-back or parallel requests to one pod fail (TC-11, TC-12). |
| DEF-02 | Low | Response has no `Content-Type` header or reason phrase, only `HTTP/1.1 200` and the body (TC-10). |
| DEF-03 | High | Consequence of DEF-01: a burst of 20 requests in 4 parallel streams through the ingress got 8–11 × `502 Bad Gateway` across runs on the 2-replica deployment (TC-18). |

Also found and fixed while testing: an unquoted `name:` with a colon made the workflow YAML invalid, ShellCheck failed on an
unused variable, and the deploy step put the mixed-case GitHub owner into the image reference (registry paths must be lowercase).
`actionlint`, ShellCheck and kubeconform are now clean (`docs/outputs/12_static_checks.txt`).

## Running the tests

```bash
pip install -r tests/requirements.txt

# 1) against a local container
docker build -t wisecow:qa . && docker run -d --name wisecow-qa -p 18081:4499 wisecow:qa
WISECOW_URL=http://127.0.0.1:18081 python -m pytest -v

# 2) also against the cluster's TLS ingress (see section 2 for the port-forwards)
WISECOW_URL=http://127.0.0.1:18081 \
WISECOW_TLS_URL=https://wisecow.local:8443 \
WISECOW_HTTP_INGRESS_URL=http://wisecow.local:8080 \
KUBECTL=kubectl python -m pytest -v

python -m pytest -m smoke          # 3 quick smoke checks only
```

| File | Covers |
|---|---|
| `tests/test_http_service.py` | TC-01–12: status, `<pre>` body, cow art, fortune variety, paths and methods, latency, pacing, headers, back-to-back and parallel requests |
| `tests/test_tls_ingress.py` | TC-13–18: HTTPS, TLS ≥ 1.2, TLS 1.1 refused, HSTS, HTTP→HTTPS redirect, burst through the ingress |
| `tests/test_static_config.py` | TC-19–25: Dockerfile hardening, manifest structure, selectors, TLS wiring, workflow shape, `kubectl --dry-run=client` |
| `tests/test_scripts.py` | TC-26–35: unit tests for the log analyzer and the health checker (local `http.server` fixture) |
| `tests/TEST_PLAN.md` | scope, approach, environment, entry/exit criteria, all 35 cases with actual results, defect log |

Environment knobs: `WISECOW_PACE` (seconds between requests, default 0.8) and `WISECOW_RETRIES` (default 2) — the
functional tests pace and retry so they test behaviour, while TC-11/TC-12 measure DEF-01 on purpose.

## Repository layout

| Path | What it is |
|---|---|
| `wisecow.sh` | the upstream app (unchanged apart from honouring `$SRVPORT`) |
| `Dockerfile` | Debian-slim image, non-root user, HEALTHCHECK |
| `k8s/` | Namespace, Deployment (2 replicas, probes, limits, hardened securityContext), ClusterIP + NodePort Service, TLS Ingress, kustomization |
| `k8s/tls/` | cert-manager ClusterIssuer + Certificate, openssl fallback, Let's Encrypt example |
| `.github/workflows/ci-cd.yaml` | lint → **qa-tests** → build & push to GHCR → deploy (CD) |
| `scripts/` | PS2 scripts |
| `tests/` | pytest suite + test plan |
| `docs/outputs/`, `docs/screenshots/` | captured terminal output and screenshots of every step |

## 1. Build and run with Docker

```bash
docker build -t ghcr.io/praneethrayalu/wisecow:latest .
docker run -d --rm --name wisecow -p 4499:4499 ghcr.io/praneethrayalu/wisecow:latest
curl -s http://localhost:4499        # ASCII cow with a fortune
docker ps                             # STATUS shows (healthy) after ~30 s
```

## 2. Deploy to Kubernetes

Tested on kind v0.27 / Kubernetes v1.32, ingress-nginx v1.12.1, cert-manager v1.17.1.

```bash
# cluster prerequisites (once)
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.12.1/deploy/static/provider/kind/deploy.yaml
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.17.1/cert-manager.yaml
kubectl -n ingress-nginx wait --for=condition=ready pod -l app.kubernetes.io/component=controller --timeout=180s
kubectl -n cert-manager wait --for=condition=ready pod --all --timeout=180s

# wisecow
kind load docker-image ghcr.io/praneethrayalu/wisecow:latest --name wisecow   # kind only; a real cluster pulls from GHCR
kubectl apply -f k8s/namespace.yaml -f k8s/deployment.yaml -f k8s/service.yaml
kubectl apply -f k8s/tls/cert-manager.yaml -f k8s/ingress.yaml
kubectl -n wisecow rollout status deployment/wisecow
kubectl -n wisecow get all,certificate,ingress
```

Access:

```bash
kubectl -n wisecow port-forward svc/wisecow 8080:80 && curl http://localhost:8080          # plain, via the Service
# TLS via the ingress (hostname wisecow.local)
echo "127.0.0.1 wisecow.local" | sudo tee -a /etc/hosts
kubectl -n ingress-nginx port-forward svc/ingress-nginx-controller 8443:443
curl -vk https://wisecow.local:8443/     # TLSv1.3, CN=wisecow.local, HTTP/2 200
curl -I  http://wisecow.local:8080/      # 308 redirect to https (ssl-redirect annotation)
```

## 3. CI/CD (GitHub Actions)

`.github/workflows/ci-cd.yaml` runs on every push to `main`:

1. **lint-and-test** — ShellCheck on `wisecow.sh` and the scripts, `kubeconform` on the manifests, `py_compile` on the Python scripts and tests.
2. **qa-tests** — builds the image, starts it, runs the pytest suite against it and uploads `pytest-report.xml` (JUnit) as an artifact. The image is only pushed if this job passes.
3. **build-and-push** — Docker Buildx builds the image and pushes `ghcr.io/<owner>/wisecow:latest` and `:sha-<short>` to GitHub Container Registry using the automatic `GITHUB_TOKEN` (no extra secret needed). Pull requests only build + smoke-test.
4. **deploy** (Continuous Deployment, challenge goal) — decodes the `KUBE_CONFIG` repository secret (base64 kubeconfig of a service account scoped to the `wisecow` namespace), applies the manifests, pins the Deployment to the image **digest** just built, waits for the rollout and runs `scripts/app_health_checker.py` against the service.

Secrets to add in GitHub → Settings → Secrets and variables → Actions: `KUBE_CONFIG`. Make the GHCR package public (Packages → wisecow → Package settings) or add an `imagePullSecret`.

## 4. TLS (challenge goal)

The Ingress terminates TLS with the `wisecow-tls` secret. cert-manager issues it from a self-signed
ClusterIssuer here (no public DNS in a local cluster); swap `issuerRef` to `letsencrypt-prod`
(`k8s/tls/letsencrypt-issuer.example.yaml`) for a real domain. `make-selfsigned-secret.sh` creates the
same secret with openssl if cert-manager is not installed. HTTP is redirected to HTTPS (308).

## 5. Problem Statement 2 — scripts

```bash
python3 scripts/app_health_checker.py http://localhost:8080 https://wisecow.local:8443/ --insecure --retries 3
./scripts/system_health_monitor.sh -c 80 -m 80 -d 85 -p 400 -i 60      # loop every 60 s, alerts to console + system_health.log
python3 scripts/log_analyzer.py /var/log/nginx/access.log --top 10
```

## 6. Bonus — KubeArmor zero-trust policy (not required for QA)

`kubearmor/wisecow-zero-trust-policy.yaml` allow-lists only the binaries the app needs (bash, nc, fortune,
cowsay/perl, coreutils), makes `/etc`, `/usr/share`, `/proc` read-only, blocks `/etc/shadow`, `/tmp`,
`/root` and the service-account token directory, blocks `net_raw`/`sys_admin`, and the namespace
annotations in `namespace-default-posture.yaml` switch the default posture to **block** so anything not
listed is denied. Install KubeArmor with `karmor install`, then `kubectl apply -f kubearmor/`.

Honest note: on the sandbox used for this write-up the node kernel exposed no BPF-LSM/AppArmor, so KubeArmor
ran in visibility-only mode — the policy was accepted and applied (CRD validation passed) but enforcement was
not demonstrated there; on GKE/EKS/AKS or a kind node with AppArmor it enforces as written.

## Upstream

Original app: https://github.com/nyrahul/wisecow (see `UPSTREAM.md`).
