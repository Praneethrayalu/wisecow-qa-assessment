# Test plan and test cases - Wisecow (AccuKnox QA Trainee)

Author: Repalle Praneeth Rayalu · Last full run: 15 Oct 2026, 11:07 UTC

## 1. Scope
The Wisecow web app (`wisecow.sh`), its Docker image, the Kubernetes manifests (Deployment, Service,
Ingress, TLS via cert-manager, optional NodePort, KubeArmor policy), the GitHub Actions workflow and the
PS2 ops scripts. Out of scope: load/performance testing beyond small bursts, KubeArmor enforcement (needs a
kernel with BPF-LSM/AppArmor, not available on the test machine), and any cloud deployment.

## 2. Approach (test layers)
| Layer | Marker | Needs | Runs in CI |
|---|---|---|---|
| Unit - request handler (`wisecow.sh --handle` with stub fortune/cowsay) and PS2 scripts | `unit` | Python, bash | yes (`static-and-unit`) |
| Static - Dockerfile, manifests, KubeArmor policy, workflow, kubeconform | `static` | Python, kubectl/kubeconform (skips with a reason if missing) | yes |
| Live HTTP black-box | `live` | a running app: `WISECOW_URL` or `WISECOW_IMAGE` | yes (`qa-tests`, against the image just built) |
| Docker runtime (non-root, read-only, HEALTHCHECK, graceful stop) | `docker` | `WISECOW_IMAGE` + Docker | yes |
| Ingress / TLS / live cluster | `cluster` | ingress + CA file + kubectl | no - run on a real cluster (kind here) |

Rules: every network call has a timeout; the suite starts its own containers and always removes them;
tests do not depend on each other's order; missing infrastructure gives **SKIPPED with a "not run:"
reason**, or a **failure** when `WISECOW_REQUIRE_LIVE=1` / `WISECOW_REQUIRE_CLUSTER=1` (CI sets the first,
so a missing app can never produce a green run). JUnit XML: `--junitxml=<file>`.

## 3. Environment of the recorded run
Debian 12 test machine, Docker, kind (Kubernetes v1.32) with ingress-nginx and cert-manager, Python 3.11,
fresh virtualenv from `tests/requirements.txt` (pytest 8.3.3, requests 2.32.3, PyYAML 6.0.2,
pytest-timeout 2.3.1). Image under test: built from this repo (`ghcr.io/praneethrayalu/wisecow:final`
locally). Ingress reached through `kubectl -n ingress-nginx port-forward svc/ingress-nginx-controller 8443:443 8080:80`.

## 4. Entry and exit criteria
Entry: image builds; manifests render with `kubectl kustomize k8s`. Exit: 0 failed, 0 xfail for fixable
defects; every skipped or not-run test has a written reason; all defects below are closed or explained.

## 5. How to run
```bash
python -m venv .venv && . .venv/bin/activate && pip install -r tests/requirements.txt
pytest -m "unit or static"                                    # no app needed
WISECOW_IMAGE=wisecow:local pytest -m "live or docker"        # suite starts/stops the container
WISECOW_TLS_URL=https://wisecow.local:8443 WISECOW_CA_CERT=wisecow-ca.crt \
WISECOW_HTTP_INGRESS_URL=http://wisecow.local:8080 KUBECTL=kubectl pytest -m cluster
```

## 6. Results
| Run | Passed | Failed | Skipped | xfail | Not run | Evidence |
|---|---|---|---|---|---|---|
| Full suite, all layers, live + cluster required | **101** | 0 | 0 | 0 | 0 | `docs/evidence/03_pytest_full_suite.txt`, `junit-full.xml` |
| CI split: `unit or static` | 57 | 0 | 0 | 0 | 44 deselected (other layers) | `docs/evidence/04_pytest_ci_split_static_unit.txt` |
| New live tests vs the **original** image (regression proof) | 1 | **29** | 0 | 0 | - | `docs/evidence/05_new_live_tests_vs_ORIGINAL_image.txt` |
| Previous version of this suite (baseline, original app) | 31 | 0 | 0 | 4 | - | `docs/baseline/outputs/` |

101 = 69 test functions, some parametrized (e.g. 5 methods for TC-05, 7 malformed lines for TC-07).
Not yet executed: the GitHub Actions run itself (the repo has not been pushed when this was written).

## 7. Defect log
| ID | Severity | Defect | Found by | Fix | Status / verified by |
|---|---|---|---|---|---|
| DEF-01 | High | `nc -l` loop serves one connection then restarts: back-to-back and parallel requests are reset (10 of 20 back-to-back failed) | old TC-11/12 (xfail) | `socat ... fork` listener, one handler per connection | **Closed** - TC-11, TC-12, TC-13 pass; fail on the original image |
| DEF-02 | Low | No `Content-Type`/`Content-Length`, no reason phrase; every method and path answered the same | old TC-10 (xfail) | full HTTP response, 400/404/405/408/413/431 | **Closed** - TC-03..TC-09 pass |
| DEF-03 | High | Burst through the ingress: 8-11 of 20 requests got 502 | old TC-18 (xfail) | consequence of DEF-01, same fix; HTTP readiness probes | **Closed** - TC-32: 50/50 HTTP 200 (and 100/100 in a curl burst) |
| DEF-04 | Medium | HTML escaping in the new handler produced `<lt;` (Bash 5.2 treats `&` in a replacement as the matched text) | new TC-17 | quote the replacement strings | **Closed** - TC-17 passes |
| DEF-05 | Medium | Pipeline bugs in the first version: invalid workflow YAML, upper-case GHCR name, shellcheck failure | static review | fixed; actionlint/shellcheck clean | **Closed** - TC-43, `01_static_checks.txt` |

No test is marked xfail any more.

## 8. Traceability (old IDs → new)
Old TC-01..09 (basic HTTP) → TC-01, TC-02, TC-14, TC-15 · old TC-10 → TC-03 · old TC-11/12 → TC-11/12 ·
old TC-13..17 (TLS) → TC-25, TC-28..31 · old TC-18 → TC-32 · old TC-19..25 (static) → TC-34..45 ·
old TC-26..35 (scripts) → TC-46..68. New areas: methods/paths/malformed requests (TC-04..10), handler unit
tests (TC-17..20b), Docker runtime (TC-21..24), certificate trust and hostname validation (TC-26, TC-27),
live deployment hardening (TC-33), KubeArmor and workflow gates (TC-42, TC-43).

## 9. Test cases
| ID | Layer | What is checked (expected result) | Test | Result 15 Oct 2026 |
|---|---|---|---|---|
| TC-01 | Live HTTP | GET / answers 200. | `test_http_service.py::test_get_root_returns_200` | Pass |
| TC-02 | Live HTTP | the body is cowsay art inside <pre>. | `test_http_service.py::test_body_is_a_cow_in_pre` | Pass |
| TC-03 | Live HTTP | (was DEF-02) valid status line, Content-Type and an exact Content-Length. | `test_http_service.py::test_headers_content_type_and_length` | Pass |
| TC-04 | Live HTTP | HEAD / is 200 with headers and no body. | `test_http_service.py::test_head_returns_headers_without_body` | Pass |
| TC-05 | Live HTTP | methods other than GET/HEAD get 405 with an Allow header. | `test_http_service.py::test_unsupported_methods_get_405` | 5/5 passed |
| TC-06 | Live HTTP | only / serves the cow (a query string is ignored); other paths are 404. | `test_http_service.py::test_paths` | 5/5 passed |
| TC-07 | Live HTTP | a malformed request line gets a clean 400, not a hang or a reset. | `test_http_service.py::test_malformed_request_line_gets_400` | 7/7 passed |
| TC-08 | Live HTTP | more than 100 header lines are refused with 431. | `test_http_service.py::test_too_many_headers_gets_431` | Pass |
| TC-09 | Live HTTP | a client that connects and sends nothing is answered 408 after the read timeout (5 s). | `test_http_service.py::test_silent_client_gets_408` | Pass |
| TC-10 | Live HTTP | clients that vanish mid-request (with and without a partial request) leave the server healthy. | `test_http_service.py::test_abrupt_disconnects_do_not_break_the_server` | Pass |
| TC-11 | Live HTTP | (was DEF-01) 30 sequential requests with no pause all get 200. | `test_http_service.py::test_back_to_back_requests_all_succeed` | Pass |
| TC-12 | Live HTTP | (was DEF-01) 30 parallel requests all get 200. | `test_http_service.py::test_concurrent_requests_all_succeed` | Pass |
| TC-13 | Live HTTP | a client that would reuse connections (requests.Session) still gets correct answers. | `test_http_service.py::test_keep_alive_client_is_served` | Pass |
| TC-14 | Live HTTP | the wisdom is random - 10 requests give at least 2 different bodies. | `test_http_service.py::test_fortunes_vary` | Pass |
| TC-15 | Live HTTP | sanity bound, not a benchmark - median under 1 s and worst under 3 s over 10 requests. | `test_http_service.py::test_latency_is_reasonable` | Pass |
| TC-16 | Live HTTP | the health checker reports a closed port as DOWN (service unavailability). | `test_http_service.py::test_unreachable_service_is_reported_down` | Pass |
| TC-17 | Unit (handler) | fortune text with <, >, & is HTML-escaped; Content-Length equals the body size. | `test_handler_unit.py::test_handler_escapes_html_and_sets_exact_length` | Pass |
| TC-18 | Unit (handler) | if fortune/cowsay fail, the client gets a 500 instead of an empty 200. | `test_handler_unit.py::test_handler_returns_500_when_fortune_fails` | Pass |
| TC-19 | Unit (handler) | a POST body is read and discarded, then 405 is sent (no reset of the client). | `test_handler_unit.py::test_handler_discards_request_body` | Pass |
| TC-20 | Unit (handler) | a declared body over 1 MiB is refused with 413. | `test_handler_unit.py::test_handler_rejects_huge_body` | Pass |
| TC-20b | Unit (handler) | starting the server without cowsay/fortune/socat fails fast with a clear message. | `test_handler_unit.py::test_handler_missing_prerequisite_is_reported` | Pass |
| TC-21 | Docker runtime | the process runs as uid 10001, not root. | `test_docker_runtime.py::test_container_runs_as_non_root` | Pass |
| TC-22 | Docker runtime | with --read-only the app still serves, and its own script cannot be modified. | `test_docker_runtime.py::test_root_filesystem_is_read_only_and_app_still_serves` | Pass |
| TC-23 | Docker runtime | the image HEALTHCHECK (a real HTTP request) turns the container healthy. | `test_docker_runtime.py::test_healthcheck_reports_healthy` | Pass |
| TC-24 | Docker runtime | SIGTERM stops the container quickly (socat is PID 1 and handles it), no 10 s kill. | `test_docker_runtime.py::test_graceful_stop_is_fast` | Pass |
| TC-25 | Ingress/TLS/cluster | HTTPS works with full verification (CA + hostname wisecow.local). | `test_tls_ingress.py::test_https_serves_the_cow_with_verified_certificate` | Pass |
| TC-26 | Ingress/TLS/cluster | with the normal public trust store the self-signed certificate is REJECTED. | `test_tls_ingress.py::test_self_signed_certificate_is_not_publicly_trusted` | Pass |
| TC-27 | Ingress/TLS/cluster | hostname validation - connecting with SNI 'evil.example' does not yield a trusted cert. | `test_tls_ingress.py::test_wrong_hostname_is_rejected` | Pass |
| TC-28 | Ingress/TLS/cluster | TLS 1.2 or 1.3 is negotiated. | `test_tls_ingress.py::test_modern_tls_negotiated` | Pass |
| TC-29 | Ingress/TLS/cluster | TLS 1.0 and 1.1 handshakes are refused. | `test_tls_ingress.py::test_obsolete_tls_rejected` | 2/2 passed |
| TC-30 | Ingress/TLS/cluster | HTTPS responses carry Strict-Transport-Security. | `test_tls_ingress.py::test_hsts_header` | Pass |
| TC-31 | Ingress/TLS/cluster | plain HTTP is redirected (308) to https://. | `test_tls_ingress.py::test_http_redirects_to_https` | Pass |
| TC-32 | Ingress/TLS/cluster | (was DEF-03) 50 requests, 10 at a time, through ingress -> 2 replicas: all 200, no 502. | `test_tls_ingress.py::test_burst_through_ingress` | Pass |
| TC-33 | Ingress/TLS/cluster | the live Deployment has all replicas ready and runs with the hardened securityContext. | `test_tls_ingress.py::test_live_deployment_healthy_and_hardened` | Pass |
| TC-34 | Static config | base pinned by digest, non-root numeric USER, HTTP HEALTHCHECK, no ADD, apt lists removed. | `test_static_config.py::test_dockerfile_hardening` | Pass |
| TC-35 | Static config | .git, CI files, manifests, tests and docs are not sent into the image. | `test_static_config.py::test_dockerignore_keeps_build_context_small` | Pass |
| TC-36 | Static config | Namespace, Deployment, Service and Ingress exist and share the wisecow namespace. | `test_static_config.py::test_required_kinds_present` | Pass |
| TC-37 | Static config | non-root, seccomp RuntimeDefault, no privilege escalation, drop ALL, read-only rootfs, no SA token. | `test_static_config.py::test_deployment_security_context` | Pass |
| TC-38 | Static config | HTTP startup/readiness/liveness probes, requests+limits, >=2 replicas, safe rolling update. | `test_static_config.py::test_deployment_probes_resources_rollout` | Pass |
| TC-39 | Static config | Service selects the pod labels and targets the named port 'http'; Ingress points at it. | `test_static_config.py::test_selectors_and_named_ports` | Pass |
| TC-40 | Static config | Ingress TLS secret and host match the cert-manager Certificate; HTTPS redirect forced. | `test_static_config.py::test_ingress_tls_matches_certificate` | Pass |
| TC-41 | Static config | the NodePort (plain HTTP, bypasses TLS) is optional and not in kustomization.yaml. | `test_static_config.py::test_nodeport_not_deployed_by_default` | Pass |
| TC-42 | Static config | KubeArmor policy is an allow-list for the wisecow pod labels, with block posture. | `test_static_config.py::test_kubearmor_policy_targets_the_pods` | Pass |
| TC-43 | Static config | least-privilege permissions; push only after tests; deploy needs push, uses a digest, | `test_static_config.py::test_workflow_gates_and_permissions` | Pass |
| TC-44 | Static config | `kubectl kustomize k8s` renders exactly the 4 app objects (pure client-side, no cluster). | `test_static_config.py::test_kustomize_renders` | Pass |
| TC-45 | Static config | every manifest (app, TLS, optional, KubeArmor) validates against its schema. | `test_static_config.py::test_kubeconform_schema_validation` | Pass |
| TC-46 | Unit (PS2 scripts) | health up. | `test_scripts.py::test_health_up` | Pass |
| TC-47 | Unit (PS2 scripts) | health http error is down. | `test_scripts.py::test_health_http_error_is_down` | 2/2 passed |
| TC-48 | Unit (PS2 scripts) | health custom expected status. | `test_scripts.py::test_health_custom_expected_status` | Pass |
| TC-49 | Unit (PS2 scripts) | health connection refused is down. | `test_scripts.py::test_health_connection_refused_is_down` | Pass |
| TC-50 | Unit (PS2 scripts) | health timeout is down. | `test_scripts.py::test_health_timeout_is_down` | Pass |
| TC-51 | Unit (PS2 scripts) | health retries then gives up without trailing sleep. | `test_scripts.py::test_health_retries_then_gives_up_without_trailing_sleep` | Pass |
| TC-52 | Unit (PS2 scripts) | health cli json stdout is pure json. | `test_scripts.py::test_health_cli_json_stdout_is_pure_json` | Pass |
| TC-53 | Unit (PS2 scripts) | health cli exit zero when all up. | `test_scripts.py::test_health_cli_exit_zero_when_all_up` | Pass |
| TC-54 | Unit (PS2 scripts) | health cli rejects bad arguments. | `test_scripts.py::test_health_cli_rejects_bad_arguments` | 7/7 passed |
| TC-55 | Unit (PS2 scripts) | health cli rejects bad urls. | `test_scripts.py::test_health_cli_rejects_bad_urls` | 3/3 passed |
| TC-56 | Unit (PS2 scripts) | log counts. | `test_scripts.py::test_log_counts` | Pass |
| TC-57 | Unit (PS2 scripts) | log malformed lines are counted not dropped. | `test_scripts.py::test_log_malformed_lines_are_counted_not_dropped` | Pass |
| TC-58 | Unit (PS2 scripts) | log gzip and multiple files. | `test_scripts.py::test_log_gzip_and_multiple_files` | Pass |
| TC-59 | Unit (PS2 scripts) | log cli json is valid. | `test_scripts.py::test_log_cli_json_is_valid` | Pass |
| TC-60 | Unit (PS2 scripts) | log cli text report. | `test_scripts.py::test_log_cli_text_report` | Pass |
| TC-61 | Unit (PS2 scripts) | log cli all malformed exits 1. | `test_scripts.py::test_log_cli_all_malformed_exits_1` | Pass |
| TC-62 | Unit (PS2 scripts) | log cli missing file exits 2. | `test_scripts.py::test_log_cli_missing_file_exits_2` | Pass |
| TC-63 | Unit (PS2 scripts) | log cli unreadable file exits 2. | `test_scripts.py::test_log_cli_unreadable_file_exits_2` | Pass |
| TC-64 | Unit (PS2 scripts) | log cli corrupt gzip exits 2. | `test_scripts.py::test_log_cli_corrupt_gzip_exits_2` | Pass |
| TC-65 | Unit (PS2 scripts) | log cli rejects bad top. | `test_scripts.py::test_log_cli_rejects_bad_top` | Pass |
| TC-66 | Unit (PS2 scripts) | monitor json mode healthy. | `test_scripts.py::test_monitor_json_mode_healthy` | Pass |
| TC-67 | Unit (PS2 scripts) | monitor alert exit 1. | `test_scripts.py::test_monitor_alert_exit_1` | Pass |
| TC-68 | Unit (PS2 scripts) | monitor rejects bad arguments. | `test_scripts.py::test_monitor_rejects_bad_arguments` | 9/9 passed |
