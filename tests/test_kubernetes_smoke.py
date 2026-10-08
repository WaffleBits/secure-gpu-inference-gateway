import copy
import unittest

from scripts.kubernetes_smoke import validate_runtime_contract


LABELS = {"app.kubernetes.io/name": "secure-gpu-inference-gateway"}


def kubernetes_objects():
    container = {
        "name": "gateway",
        "image": "secure-gpu-inference-gateway:0.1.0",
        "imagePullPolicy": "IfNotPresent",
        "envFrom": [{"configMapRef": {"name": "gateway-config"}}],
        "ports": [{"name": "http", "containerPort": 8000}],
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
            "readOnlyRootFilesystem": True,
        },
        "resources": {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": "500m", "memory": "512Mi"},
        },
        "readinessProbe": {"httpGet": {"path": "/health", "port": "http"}},
        "livenessProbe": {"httpGet": {"path": "/health", "port": "http"}},
    }
    pod_spec = {
        "automountServiceAccountToken": False,
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 10001,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "containers": [container],
    }
    template = {"metadata": {"labels": LABELS}, "spec": pod_spec}
    deployment = {
        "spec": {
            "replicas": 2,
            "selector": {"matchLabels": LABELS},
            "template": template,
        }
    }
    pods = {
        "items": [
            {
                "metadata": {"labels": LABELS},
                "spec": copy.deepcopy(pod_spec),
                "status": {
                    "phase": "Running",
                    "containerStatuses": [{"name": "gateway", "ready": True}],
                },
            }
            for _ in range(2)
        ]
    }
    service = {
        "spec": {
            "type": "ClusterIP",
            "selector": LABELS,
            "ports": [{"name": "http", "port": 8000, "targetPort": "http"}],
        }
    }
    configmap = {
        "data": {
            "ALLOW_DEMO_PRINCIPALS": "false",
            "AUDIT_LOG_PATH": "/var/log/gateway/audit.log",
        }
    }
    return configmap, deployment, pods, service


class KubernetesRuntimeContractTests(unittest.TestCase):
    def setUp(self):
        self.objects = kubernetes_objects()

    def test_accepts_ready_hardened_workload_and_internal_service(self):
        validate_runtime_contract(*self.objects)

    def test_rejects_enabled_demo_principals(self):
        self.objects[0]["data"]["ALLOW_DEMO_PRINCIPALS"] = "true"
        with self.assertRaisesRegex(ValueError, "ALLOW_DEMO_PRINCIPALS"):
            validate_runtime_contract(*self.objects)

    def test_rejects_service_account_token_mounting(self):
        self.objects[1]["spec"]["template"]["spec"]["automountServiceAccountToken"] = True
        with self.assertRaisesRegex(ValueError, "service account token"):
            validate_runtime_contract(*self.objects)

    def test_rejects_not_ready_or_underscaled_pods(self):
        self.objects[2]["items"][1]["status"]["containerStatuses"][0]["ready"] = False
        with self.assertRaisesRegex(ValueError, "gateway container is not ready"):
            validate_runtime_contract(*self.objects)

        self.objects = kubernetes_objects()
        self.objects[2]["items"].pop()
        with self.assertRaisesRegex(ValueError, "ready replicas"):
            validate_runtime_contract(*self.objects)

    def test_rejects_privileged_or_writable_container(self):
        self.objects[2]["items"][0]["spec"]["containers"][0]["securityContext"]["privileged"] = True
        with self.assertRaisesRegex(ValueError, "privileged"):
            validate_runtime_contract(*self.objects)

        self.objects = kubernetes_objects()
        self.objects[2]["items"][0]["spec"]["containers"][0]["securityContext"]["readOnlyRootFilesystem"] = False
        with self.assertRaisesRegex(ValueError, "read-only root filesystem"):
            validate_runtime_contract(*self.objects)

    def test_rejects_external_service_type(self):
        self.objects[3]["spec"]["type"] = "NodePort"
        with self.assertRaisesRegex(ValueError, "ClusterIP"):
            validate_runtime_contract(*self.objects)

    def test_rejects_missing_cpu_or_memory_limits(self):
        self.objects[2]["items"][0]["spec"]["containers"][0]["resources"]["limits"].pop("memory")
        with self.assertRaisesRegex(ValueError, "CPU and memory requests and limits"):
            validate_runtime_contract(*self.objects)

    def test_rejects_missing_live_health_probe(self):
        self.objects[2]["items"][0]["spec"]["containers"][0]["readinessProbe"]["httpGet"]["path"] = "/"
        with self.assertRaisesRegex(ValueError, "readinessProbe"):
            validate_runtime_contract(*self.objects)


if __name__ == "__main__":
    unittest.main()
