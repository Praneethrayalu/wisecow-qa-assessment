"""Static checks on the Dockerfile, Kubernetes manifests and CI workflow (no cluster needed,
except the optional kubectl dry-run)."""
import os
import shlex
import shutil
import subprocess

import pytest
import yaml


def load_all(path):
    with open(path) as fh:
        return [d for d in yaml.safe_load_all(fh) if d]


@pytest.fixture(scope="module")
def manifests(repo):
    docs = []
    for name in ("namespace.yaml", "deployment.yaml", "service.yaml", "ingress.yaml"):
        docs += load_all(repo / "k8s" / name)
    return {(d["kind"], d["metadata"]["name"]): d for d in docs}


def test_tc19_dockerfile_hardening(repo):
    text = (repo / "Dockerfile").read_text()
    lines = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
    assert lines[0].startswith("FROM ") and ":latest" not in lines[0], "base image must be pinned"
    users = [l for l in lines if l.startswith("USER ")]
    assert users and users[-1].split()[1] not in ("root", "0"), "container must not run as root"
    assert any(l.startswith("HEALTHCHECK") for l in lines)
    assert any(l.startswith("EXPOSE") and "4499" in l for l in lines)
    for pkg in ("fortune", "cowsay", "netcat"):
        assert pkg in text, f"{pkg} not installed"


def test_tc20_manifests_parse_and_cover_required_kinds(manifests):
    kinds = {k for k, _ in manifests}
    assert {"Namespace", "Deployment", "Service", "Ingress"} <= kinds


def test_tc21_deployment_is_production_shaped(manifests):
    dep = manifests[("Deployment", "wisecow")]
    spec = dep["spec"]
    pod = spec["template"]["spec"]
    c = pod["containers"][0]
    assert spec["replicas"] >= 2
    assert c["readinessProbe"] and c["livenessProbe"]
    assert c["resources"]["limits"]["memory"] and c["resources"]["requests"]["cpu"]
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert c["securityContext"]["allowPrivilegeEscalation"] is False
    assert "ALL" in c["securityContext"]["capabilities"]["drop"]
    assert c["ports"][0]["containerPort"] == 4499


def test_tc22_service_selector_matches_pods(manifests):
    labels = manifests[("Deployment", "wisecow")]["spec"]["template"]["metadata"]["labels"]
    for (kind, name), d in manifests.items():
        if kind == "Service":
            sel = d["spec"]["selector"]
            assert all(labels.get(k) == v for k, v in sel.items()), f"{name} selects nothing"
            assert d["spec"]["ports"][0]["targetPort"] in ("http", 4499)


def test_tc23_ingress_tls_wired_to_certificate(repo, manifests):
    ing = manifests[("Ingress", "wisecow")]
    secret = ing["spec"]["tls"][0]["secretName"]
    certs = [d for d in load_all(repo / "k8s" / "tls" / "cert-manager.yaml") if d["kind"] == "Certificate"]
    assert certs and certs[0]["spec"]["secretName"] == secret
    hosts = set(ing["spec"]["tls"][0]["hosts"])
    assert hosts <= set(certs[0]["spec"]["dnsNames"])
    backend = ing["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]["name"]
    assert ("Service", backend) in manifests


def test_tc24_workflow_build_push_deploy(repo):
    wf = yaml.safe_load((repo / ".github" / "workflows" / "ci-cd.yaml").read_text())
    triggers = wf.get("on", wf.get(True))          # PyYAML reads the key `on` as True
    assert "push" in triggers
    jobs = wf["jobs"]
    steps = " ".join(str(s) for j in jobs.values() for s in j.get("steps", []))
    assert "docker/build-push-action" in steps and "docker/login-action" in steps
    assert "kubectl" in steps and "rollout status" in steps
    deploy = [j for j in jobs.values() if "KUBE_CONFIG" in str(j)]
    assert deploy and deploy[0].get("needs"), "deploy must wait for the build"
    assert "qa-tests" in jobs, "QA job missing"


def test_tc25_kubectl_client_dry_run(repo):
    kubectl = shlex.split(os.environ.get("KUBECTL", "kubectl"))
    if not shutil.which(kubectl[-1] if kubectl[0] == "sudo" else kubectl[0]):
        pytest.skip("kubectl not installed")
    r = subprocess.run(kubectl + ["apply", "--dry-run=client", "-k", str(repo / "k8s"), "-o", "name"],
                       capture_output=True, text=True, timeout=60)
    no_cluster = ("refused", "openapi", "unable to connect", "couldn't get current server", "no configuration")
    if r.returncode != 0 and any(m in r.stderr.lower() for m in no_cluster):
        pytest.skip("kubectl has no reachable cluster for discovery (schemas are still checked by kubeconform in CI)")
    assert r.returncode == 0, r.stderr
    for want in ("namespace/wisecow", "deployment.apps/wisecow", "service/wisecow", "ingress.networking.k8s.io/wisecow"):
        assert want in r.stdout
