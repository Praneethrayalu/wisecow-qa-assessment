"""Static checks of the Dockerfile, Kubernetes manifests, KubeArmor policy and CI workflow
(TC-34 .. TC-44). No running app or cluster needed.
"""
import re
import shutil
import subprocess

import pytest
import yaml

pytestmark = pytest.mark.static


def load_all(path):
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


@pytest.fixture(scope="module")
def manifests(repo):
    docs = []
    for name in ("namespace", "deployment", "service", "ingress"):
        docs += load_all(repo / "k8s" / f"{name}.yaml")
    return docs


def kind(docs, k):
    return [d for d in docs if d["kind"] == k]


@pytest.fixture(scope="module")
def deployment(manifests):
    return kind(manifests, "Deployment")[0]


@pytest.fixture(scope="module")
def workflow(repo):
    return yaml.safe_load((repo / ".github/workflows/ci-cd.yaml").read_text())


def test_dockerfile_hardening(repo):
    """TC-34: base pinned by digest, non-root numeric USER, HTTP HEALTHCHECK, no ADD, apt lists removed."""
    df = (repo / "Dockerfile").read_text()
    assert re.search(r"^FROM \S+@sha256:[0-9a-f]{64}", df, re.M)
    assert re.search(r"^USER 10001", df, re.M)
    assert "HEALTHCHECK" in df and "200" in df
    assert not re.search(r"^ADD ", df, re.M)
    assert "rm -rf /var/lib/apt/lists/*" in df and "--no-install-recommends" in df
    assert not re.search(r"(?i)(password|secret|token)\s*=", df)


def test_dockerignore_keeps_build_context_small(repo):
    """TC-35: .git, CI files, manifests, tests and docs are not sent into the image."""
    ignored = set((repo / ".dockerignore").read_text().split())
    assert {".git", ".github", "k8s", "tests", "docs"} <= ignored


def test_required_kinds_present(manifests):
    """TC-36: Namespace, Deployment, Service and Ingress exist and share the wisecow namespace."""
    assert {"Namespace", "Deployment", "Service", "Ingress"} <= {d["kind"] for d in manifests}
    assert all(d["metadata"].get("namespace", "wisecow") == "wisecow" for d in manifests if d["kind"] != "Namespace")


def test_deployment_security_context(deployment):
    """TC-37: non-root, seccomp RuntimeDefault, no privilege escalation, drop ALL, read-only rootfs, no SA token."""
    pod = deployment["spec"]["template"]["spec"]
    c = pod["containers"][0]
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    assert pod["automountServiceAccountToken"] is False
    sc = c["securityContext"]
    assert sc["allowPrivilegeEscalation"] is False and sc["readOnlyRootFilesystem"] is True
    assert sc["capabilities"]["drop"] == ["ALL"]


def test_deployment_probes_resources_rollout(deployment):
    """TC-38: HTTP startup/readiness/liveness probes, requests+limits, >=2 replicas, safe rolling update."""
    spec = deployment["spec"]
    c = spec["template"]["spec"]["containers"][0]
    for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
        assert c[probe]["httpGet"]["port"] == "http", probe
    assert {"cpu", "memory"} <= set(c["resources"]["requests"]) and {"cpu", "memory"} <= set(c["resources"]["limits"])
    assert spec["replicas"] >= 2
    assert spec["strategy"]["rollingUpdate"]["maxUnavailable"] == 0
    assert spec["revisionHistoryLimit"] >= 1


def test_selectors_and_named_ports(manifests, deployment):
    """TC-39: Service selects the pod labels and targets the named port 'http'; Ingress points at it."""
    labels = deployment["spec"]["template"]["metadata"]["labels"]
    assert deployment["spec"]["selector"]["matchLabels"].items() <= labels.items()
    ports = {p["name"] for p in deployment["spec"]["template"]["spec"]["containers"][0]["ports"]}
    svc = kind(manifests, "Service")[0]
    assert svc["spec"]["type"] == "ClusterIP"
    assert svc["spec"]["selector"].items() <= labels.items()
    assert svc["spec"]["ports"][0]["targetPort"] in ports
    backend = kind(manifests, "Ingress")[0]["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]
    assert backend["name"] == svc["metadata"]["name"] and backend["port"]["number"] == svc["spec"]["ports"][0]["port"]


def test_ingress_tls_matches_certificate(repo, manifests):
    """TC-40: Ingress TLS secret and host match the cert-manager Certificate; HTTPS redirect forced."""
    ing = kind(manifests, "Ingress")[0]
    cert = kind(load_all(repo / "k8s/tls/cert-manager.yaml"), "Certificate")[0]
    tls = ing["spec"]["tls"][0]
    assert tls["secretName"] == cert["spec"]["secretName"]
    assert set(tls["hosts"]) <= set(cert["spec"]["dnsNames"])
    assert ing["metadata"]["annotations"]["nginx.ingress.kubernetes.io/force-ssl-redirect"] == "true"


def test_nodeport_not_deployed_by_default(repo):
    """TC-41: the NodePort (plain HTTP, bypasses TLS) is optional and not in kustomization.yaml."""
    kust = yaml.safe_load((repo / "k8s/kustomization.yaml").read_text())
    assert not any("optional" in r for r in kust["resources"])


def test_kubearmor_policy_targets_the_pods(repo, deployment):
    """TC-42: KubeArmor policy is an allow-list for the wisecow pod labels, with block posture."""
    pol = load_all(repo / "kubearmor/wisecow-zero-trust-policy.yaml")[0]
    assert pol["spec"]["action"] == "Allow"
    assert pol["spec"]["selector"]["matchLabels"].items() <= deployment["spec"]["template"]["metadata"]["labels"].items()
    allowed = {p["path"] for p in pol["spec"]["process"]["matchPaths"]}
    assert {"/usr/bin/socat", "/usr/games/fortune", "/usr/games/cowsay", "/app/wisecow.sh"} <= allowed
    ns = load_all(repo / "kubearmor/namespace-default-posture.yaml")[0]
    assert ns["metadata"]["annotations"]["kubearmor-file-posture"] == "block"


def test_workflow_gates_and_permissions(workflow):
    """TC-43: least-privilege permissions; push only after tests; deploy needs push, uses a digest,
    checks its secret, waits for rollout and verifies health; deploy is opt-in (skipped, not faked)."""
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow["jobs"]
    assert jobs["push"]["permissions"]["packages"] == "write"
    for name, job in jobs.items():
        if name != "push":
            assert "packages" not in job.get("permissions", {}), name
    push_needs = jobs["push"]["needs"]
    push_needs = push_needs if isinstance(push_needs, list) else [push_needs]
    assert "qa-tests" in push_needs or "image-test" in push_needs
    deploy = jobs["deploy"]
    assert deploy["needs"] == "push" and "DEPLOY_ENABLED" in deploy["if"]
    steps = " ".join(str(s.get("run", "")) for s in deploy["steps"])
    assert "@sha256:" in steps and "KUBE_CONFIG" in steps and "rollout status" in steps
    assert "rollout undo" in steps and "app_health_checker.py" in steps
    assert "secrets.KUBE_CONFIG" not in steps        # secret goes through env, not inlined into scripts


def test_kustomize_renders(repo):
    """TC-44: `kubectl kustomize k8s` renders exactly the 4 app objects (pure client-side, no cluster)."""
    if not shutil.which("kubectl"):
        pytest.skip("not run: kubectl not installed")
    p = subprocess.run(["kubectl", "kustomize", str(repo / "k8s")], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    kinds = sorted(d["kind"] for d in yaml.safe_load_all(p.stdout) if d)
    assert kinds == ["Deployment", "Ingress", "Namespace", "Service"]


def test_kubeconform_schema_validation(repo):
    """TC-45: every manifest (app, TLS, optional, KubeArmor) validates against its schema."""
    if not shutil.which("kubeconform"):
        pytest.skip("not run: kubeconform not installed (CI installs it)")
    crds = "https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"
    local = str(repo / "k8s/schemas/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json")
    files = [str(p) for p in sorted((repo / "k8s").glob("*.yaml")) if p.name != "kustomization.yaml"]
    files += [str(p) for p in sorted((repo / "k8s/tls").glob("*.yaml"))]
    files += [str(p) for p in sorted((repo / "k8s/optional").glob("*.yaml"))]
    files += [str(p) for p in sorted((repo / "kubearmor").glob("*.yaml"))]
    p = subprocess.run(["kubeconform", "-strict", "-summary", "-schema-location", "default",
                        "-schema-location", crds, "-schema-location", local, *files],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
