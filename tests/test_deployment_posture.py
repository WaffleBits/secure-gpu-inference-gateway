import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentPostureTests(unittest.TestCase):
    def test_container_runs_as_dedicated_non_root_user(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("adduser --system --uid 10001", dockerfile)
        self.assertIn("USER gateway", dockerfile)
        self.assertNotIn("USER root", dockerfile)

    def test_kubernetes_workload_has_restricted_runtime_posture(self):
        manifest = (ROOT / "deploy" / "kubernetes" / "gateway.yaml").read_text(
            encoding="utf-8"
        )

        required_controls = (
            "runAsNonRoot: true",
            "allowPrivilegeEscalation: false",
            "readOnlyRootFilesystem: true",
            "- ALL",
            "type: RuntimeDefault",
        )
        for control in required_controls:
            with self.subTest(control=control):
                self.assertIn(control, manifest)

    def test_kubernetes_workload_has_health_and_resource_guards(self):
        manifest = (ROOT / "deploy" / "kubernetes" / "gateway.yaml").read_text(
            encoding="utf-8"
        )

        for control in (
            "readinessProbe:",
            "livenessProbe:",
            "resources:",
            "limits:",
            "prometheus.io/scrape: \"true\"",
        ):
            with self.subTest(control=control):
                self.assertIn(control, manifest)


if __name__ == "__main__":
    unittest.main()
