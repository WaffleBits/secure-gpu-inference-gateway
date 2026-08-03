from __future__ import annotations

import contextlib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bench import environment


@unittest.skipIf(environment.psutil is None, "psutil is not installed")
class ResourceSamplerTest(unittest.TestCase):
    def test_reuses_process_objects_for_meaningful_cpu_deltas(self) -> None:
        process = Mock()
        process.cpu_percent.side_effect = [0.0, 37.5]
        process.oneshot.return_value = contextlib.nullcontext()
        process.memory_info.return_value = SimpleNamespace(rss=1234)
        memory = SimpleNamespace(used=4321, percent=25.0)

        with (
            patch.object(
                environment.psutil, "Process", return_value=process
            ) as process_factory,
            patch.object(environment.psutil, "cpu_percent", return_value=12.5),
            patch.object(environment.psutil, "virtual_memory", return_value=memory),
            patch.object(environment.threading, "Thread") as thread,
            patch.object(environment, "query_gpu_utilization", return_value=[]),
        ):
            sampler = environment.ResourceSampler(
                interval_seconds=0.5,
                nvidia_smi_command=["nvidia-smi"],
                monitored_pids={"gateway": 123},
            )
            sampler.start()
            sample = sampler._sample()

        thread.return_value.start.assert_called_once()
        process_factory.assert_called_once_with(123)
        self.assertEqual(process.cpu_percent.call_count, 2)
        self.assertEqual(sample["processes"]["gateway"]["cpu_percent"], 37.5)
        self.assertEqual(sample["processes"]["gateway"]["rss_bytes"], 1234)


if __name__ == "__main__":
    unittest.main()
