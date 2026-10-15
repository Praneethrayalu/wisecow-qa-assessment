# Interview prep - QA Trainee (Wisecow)

## The solution in one minute
I deployed the Wisecow app (Docker + Kubernetes + TLS ingress + CI/CD + ops scripts, as the PS asks) and
built a QA layer around it: a pytest suite in five layers (unit, static, live HTTP, Docker runtime,
ingress/TLS), a test plan with 69 traceable test cases, and a defect log. My first test run found three real
defects in the app; I reproduced them, fixed the app, and turned the expected-failure tests into strict
passing tests. The final full run is 101 passed, 0 failed, 0 skipped, 0 xfail.

## How a test run flows
1. `conftest.py` reads the configuration from environment variables (`WISECOW_URL` or `WISECOW_IMAGE`,
   TLS URL, CA file, kubectl).
2. With `WISECOW_IMAGE`, a session fixture starts a hardened container on a free port, waits until it
   answers 200, yields the URL and removes the container at the end (even on failure).
3. Tests use that URL (requests with timeouts, or raw sockets for malformed requests).
4. Missing infrastructure → SKIPPED with a "not run:" reason, or FAILED when `WISECOW_REQUIRE_LIVE=1`
   (set in CI), so CI can't go green without actually testing the app.
5. `--junitxml` writes a JUnit report that CI uploads as an artifact.

## Design choices and trade-offs
- **Layers + markers** (`-m unit`, `static`, `live`, `docker`, `cluster`): fast checks run everywhere;
  cluster tests only where a cluster exists. Trade-off: CI doesn't cover ingress/TLS - I ran those on kind
  and documented it.
- **Raw-socket tests** for malformed requests: `requests` won't send a broken request line.
- **Stubbed fortune/cowsay** in handler unit tests: makes output deterministic (I can inject `<`, `&` and
  a failing fortune) without Docker.
- **No arbitrary tight latency limit**: TC-15 is only a sanity bound (median < 1 s, worst < 3 s).
- **Fixable defects are fixed, not xfailed**: an xfail would hide a regression.

## Top 3 defects found
1. **DEF-01 (High) connection resets** - the `nc -l` loop restarts after every connection; 10 of 20
   back-to-back requests failed. Fixed with a socat fork-per-connection listener. TC-11/12 now pass and
   fail again on the original image (proves the tests detect it).
2. **DEF-03 (High) 502 under a burst through the ingress** - 8-11 of 20. Same root cause. Now 50/50 (100/100
   with curl).
3. **DEF-02 (Low) invalid HTTP response** - no Content-Type/Content-Length, POST and /anything answered 200.
   Now proper headers and 404/405/400/408/413/431.
Also DEF-04: a unit test caught a bug in my own fix (Bash 5.2 `&` in HTML escaping).

## Limitations
GitHub Actions not executed on GitHub yet; ingress/TLS tests run on a local kind cluster only; no real
load/performance testing; KubeArmor enforcement not testable on that kernel.

## 10 likely questions about my work

1. **What is the difference between skipped, xfail and failed in your report?** Failed = the check ran and
   the result was wrong. Skipped = not run, with a reason (e.g. no TLS URL configured). xfail = known defect,
   expected to fail. My final run has 0 skipped and 0 xfail because I fixed the defects and gave it every
   endpoint.
2. **Why did you remove the xfail markers?** The defects were fixable within the assignment. Keeping xfail
   would make CI green while the bug exists, and a regression would go unnoticed.
3. **How do you prove the fixed defects are really fixed?** The same tests pass on the new image and fail
   on the original image (29 of 30 live tests fail there) - evidence file 05.
4. **How do you stop CI from going green when the app isn't running?** `WISECOW_REQUIRE_LIVE=1` turns
   "endpoint missing" into a failure instead of a skip.
5. **How do you clean up containers?** Fixtures start them with `docker run -d` and remove them in a
   `finally` block, so a failing test still cleans up.
6. **How do you test malformed requests?** Raw TCP sockets: I send things like `garbage`, `GET /` without a
   version, `HTTP/9.9`, lowercase methods, and check for a 400 status line.
7. **How do you test TLS properly?** Verify against the cluster's CA with hostname checking (TC-25), check
   the public trust store rejects the self-signed cert (TC-26), a wrong SNI name fails verification (TC-27),
   TLS 1.2/1.3 negotiated (TC-28), TLS 1.0/1.1 refused (TC-29), HSTS (TC-30), 308 redirect (TC-31).
8. **Why is there no strict latency test?** Latency depends on the machine; a tight limit gives flaky
   results. I keep a loose sanity bound and test correctness under concurrency instead.
9. **What does the static layer check?** Dockerfile (digest-pinned base, non-root, healthcheck), k8s security
   context, probes, resources, selectors/ports, Ingress TLS matches the Certificate, NodePort not deployed,
   KubeArmor policy targets the pods, workflow permissions and job gating, kubeconform schemas.
10. **Which test would you add next?** Running the cluster tests in CI with a kind cluster per run, and a
    KubeArmor enforcement test on a node with BPF-LSM (exec a blocked binary, expect "Permission denied").
