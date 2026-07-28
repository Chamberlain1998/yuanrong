#!/usr/bin/env python3

"""Regression tests for the Buildkite sandbox K8S smoke gate."""

import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SMOKE_SCRIPT = REPO_ROOT / ".buildkite" / "test_sandbox_k8s.sh"


class SandboxK8SSmokeContractTest(unittest.TestCase):
    def test_create_probes_do_not_send_legacy_executor_runtime(self):
        script = SMOKE_SCRIPT.read_text(encoding="utf-8")

        self.assertNotIn('"runtime":"rust"', script)

    def test_failed_probe_cannot_turn_unexecuted_smoke_green(self):
        script = SMOKE_SCRIPT.read_text(encoding="utf-8")
        failure_block = script.split(
            'if ! probe_sandbox_ready "${smoke_server_address}"; then',
            1,
        )[1].split(
            'if [[ "${YR_K8S_RUN_IDLE_TIMEOUT:-true}"',
            1,
        )[0]

        self.assertNotIn("exit 0", failure_block)
        self.assertIn("exit 1", failure_block)


if __name__ == "__main__":
    unittest.main()
