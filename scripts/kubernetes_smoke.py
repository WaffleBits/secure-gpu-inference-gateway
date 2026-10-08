from __future__ import annotations

import json
import socket
import subprocess
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


NAMESPACE = "secure-gpu-inference"
SERVICE = "secure-gpu-inference-gateway"
SELECTOR = "app.kubernetes.io/name=secure-gpu-inference-gateway"
IMAGE = "secure-gpu-inference-gateway:0.1.0"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate_runtime_contract(
    configmap: dict[str, Any],
    deployment: dict[str, Any],
    pods: dict[str, Any],
    service: dict[str, Any],
) -> None:
    """Fail closed unless the live Kubernetes objects preserve the deployment contract."""
    data = configmap.get("data", {})
    _require(data.get("ALLOW_DEMO_PRINCIPALS") == "false", "ALLOW_DEMO_PRINCIPALS must be explicitly false")

    deployment_spec = deployment.get("spec", {})
    replicas = deployment_spec.get("replicas")
    selector = deployment_spec.get("selector", {}).get("matchLabels", {})
    template = deployment_spec.get("template", {})
    template_spec = template.get("spec", {})
    labels = template.get("metadata", {}).get("labels", {})
    _require(replicas == 2, "deployment must request exactly two replicas")
    _require(bool(selector) and all(labels.get(key) == value for key, value in selector.items()), "deployment selector must match Pod labels")
    _require(template_spec.get("automountServiceAccountToken") is False, "service account token automount must be disabled")

    template_containers = template_spec.get("containers", [])
    _require(len(template_containers) == 1, "deployment must contain exactly one gateway container")
    template_container = template_containers[0]
    _require(template_container.get("image") == IMAGE, "deployment image must match the locally built review image")
    config_refs = [
        source.get("configMapRef", {}).get("name")
        for source in template_container.get("envFrom", [])
    ]
    _require("gateway-config" in config_refs, "deployment must consume gateway-config")

    items = pods.get("items", [])
    _require(len(items) == replicas, "ready replicas must match the deployment replica count")
    for pod in items:
        pod_name = pod.get("metadata", {}).get("name", "unknown")
        pod_spec = pod.get("spec", {})
        pod_security = pod_spec.get("securityContext", {})
        _require(pod.get("status", {}).get("phase") == "Running", f"Pod {pod_name} is not Running")
        _require(pod_spec.get("automountServiceAccountToken") is False, f"Pod {pod_name} must not mount a service account token")
        _require(pod_spec.get("hostNetwork") is not True, f"Pod {pod_name} must not use host networking")
        _require(pod_security.get("runAsNonRoot") is True, f"Pod {pod_name} must run as non-root")
        _require(pod_security.get("runAsUser") == 10001, f"Pod {pod_name} must run as UID 10001")
        _require(pod_security.get("seccompProfile", {}).get("type") == "RuntimeDefault", f"Pod {pod_name} must use RuntimeDefault seccomp")

        containers = pod_spec.get("containers", [])
        _require(len(containers) == 1, f"Pod {pod_name} must contain exactly one application container")
        container = containers[0]
        _require(container.get("image") == IMAGE, f"Pod {pod_name} is not running the review image")
        security = container.get("securityContext", {})
        _require(security.get("privileged") is not True, f"Pod {pod_name} container must not be privileged")
        _require(security.get("allowPrivilegeEscalation") is False, f"Pod {pod_name} must disable privilege escalation")
        _require(security.get("readOnlyRootFilesystem") is True, f"Pod {pod_name} must use a read-only root filesystem")
        dropped = security.get("capabilities", {}).get("drop", [])
        _require("ALL" in dropped, f"Pod {pod_name} must drop all Linux capabilities")
        resources = container.get("resources", {})
        requests = resources.get("requests", {})
        limits = resources.get("limits", {})
        _require(
            all(requests.get(key) for key in ("cpu", "memory"))
            and all(limits.get(key) for key in ("cpu", "memory")),
            f"Pod {pod_name} must declare CPU and memory requests and limits",
        )
        for probe_name in ("readinessProbe", "livenessProbe"):
            probe = container.get(probe_name, {}).get("httpGet", {})
            _require(
                probe.get("path") == "/health" and probe.get("port") == "http",
                f"Pod {pod_name} must configure {probe_name} against the named health port",
            )
        _require(not any(port.get("hostPort") for port in container.get("ports", [])), f"Pod {pod_name} must not bind a host port")
        statuses = pod.get("status", {}).get("containerStatuses", [])
        gateway_status = next((status for status in statuses if status.get("name") == "gateway"), None)
        _require(gateway_status is not None and gateway_status.get("ready") is True, f"Pod {pod_name} gateway container is not ready")

    service_spec = service.get("spec", {})
    _require(service_spec.get("type", "ClusterIP") == "ClusterIP", "gateway Service must remain ClusterIP")
    _require(service_spec.get("selector") == selector, "Service selector must match Deployment selector")
    service_ports = service_spec.get("ports", [])
    _require(
        len(service_ports) == 1
        and service_ports[0].get("port") == 8000
        and service_ports[0].get("targetPort") in ("http", 8000),
        "Service must expose only the named HTTP port 8000",
    )


def _kubectl_json(*args: str) -> dict[str, Any]:
    result = subprocess.run(
        ["kubectl", *args, "--namespace", NAMESPACE, "--output=json"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _request(base_url: str, path: str, *, data: bytes | None = None, headers: dict[str, str] | None = None) -> tuple[int, str, bytes]:
    request = Request(f"{base_url}{path}", data=data, headers=headers or {}, method="POST" if data is not None else "GET")
    try:
        response = urlopen(request, timeout=3)
    except HTTPError as error:
        return error.code, error.headers.get("Content-Type", ""), error.read()
    with response:
        return response.status, response.headers.get("Content-Type", ""), response.read()


def _wait_for_health(base_url: str, process: subprocess.Popen[str], timeout_seconds: float = 30) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("kubectl port-forward exited before the Service became reachable")
        try:
            status, content_type, body = _request(base_url, "/health")
            _require(status == 200 and "application/json" in content_type, "Service health endpoint did not return JSON HTTP 200")
            _require(json.loads(body).get("status") == "ok", "Service health endpoint returned an unexpected status")
            return
        except (OSError, URLError, ValueError) as error:
            last_error = error
            time.sleep(0.25)
    raise RuntimeError(f"timed out waiting for the Kubernetes Service health endpoint ({type(last_error).__name__ if last_error else 'no response'})")


def run_smoke_test() -> None:
    context = subprocess.run(
        ["kubectl", "config", "current-context"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not context.startswith("kind-"):
        raise RuntimeError("refusing to apply the smoke test outside a local Kind context")

    configmap = _kubectl_json("get", "configmap", "gateway-config")
    deployment = _kubectl_json("get", "deployment", SERVICE)
    pods = _kubectl_json("get", "pods", "--selector", SELECTOR)
    service = _kubectl_json("get", "service", SERVICE)
    validate_runtime_contract(configmap, deployment, pods, service)

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        local_port = listener.getsockname()[1]
    process = subprocess.Popen(
        [
            "kubectl",
            "port-forward",
            f"service/{SERVICE}",
            f"{local_port}:8000",
            "--namespace",
            NAMESPACE,
            "--address=127.0.0.1",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    base_url = f"http://127.0.0.1:{local_port}"
    try:
        _wait_for_health(base_url, process)
        status, content_type, body = _request(base_url, "/metrics")
        _require(status == 200 and "text/plain" in content_type, "Service metrics endpoint did not return Prometheus text HTTP 200")
        _require(b"security_gateway_model_policy_info" in body, "Service metrics did not expose the model-policy series")

        status, _, body = _request(
            base_url,
            "/v1/infer/benchmark-echo",
            data=b'{"input":"synthetic Kubernetes smoke check"}',
            headers={"Content-Type": "application/json", "X-Principal-Id": "analyst-1"},
        )
        _require(status == 403, "unauthenticated demo-principal request was not denied")
        _require(b"synthetic Kubernetes smoke check" not in body, "denial response reflected request input")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    print("Kubernetes smoke passed: two hardened ready replicas, internal Service, health/metrics 200, demo-principal inference denied (403).")


if __name__ == "__main__":
    run_smoke_test()
