"""Canonical external-target and target-isolation checks."""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace

from agentic_synth_twin.external_targets import (
    EXPECTED_EXTERNAL_TARGETS,
    ExternalTargetBank,
    ExternalTargetError,
)
from agentic_synth_twin.surge_match import ExternalTargetController, SurgeMatchError


ROOT = Path(__file__).resolve().parents[1]
TARGETS = ROOT / "examples/targets/external-v1"


class ExternalTargetBankTests(unittest.TestCase):
    def test_canonical_bank_has_stable_order_hashes_and_format(self) -> None:
        bank = ExternalTargetBank(TARGETS)
        self.assertEqual(
            [(item.target_id, item.title, item.file_name) for item in bank.targets],
            list(EXPECTED_EXTERNAL_TARGETS),
        )
        self.assertEqual(len({item.sha256 for item in bank.targets}), 10)
        self.assertEqual(len(bank.identity_sha256), 64)

    def test_hash_corruption_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "targets"
            shutil.copytree(TARGETS, copied)
            damaged = copied / EXPECTED_EXTERNAL_TARGETS[0][2]
            damaged.write_bytes(damaged.read_bytes() + b"changed")
            with self.assertRaisesRegex(ExternalTargetError, "byte count differs"):
                ExternalTargetBank(copied)

    def test_noncanonical_wav_format_is_rejected_even_with_updated_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "targets"
            shutil.copytree(TARGETS, copied)
            target = copied / EXPECTED_EXTERNAL_TARGETS[0][2]
            with wave.open(str(target), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(44_100)
                output.writeframes(b"\0\0" * 110_250)
            manifest_path = copied / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            import hashlib

            manifest["targets"][0]["bytes"] = target.stat().st_size
            manifest["targets"][0]["sha256"] = hashlib.sha256(
                target.read_bytes()
            ).hexdigest()
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ExternalTargetError, "format differs"):
                ExternalTargetBank(copied)

    def test_identity_reordering_duplicate_and_traversal_are_rejected(self) -> None:
        mutations = []
        source = json.loads((TARGETS / "manifest.json").read_text())
        reordered = json.loads(json.dumps(source))
        reordered["targets"][0], reordered["targets"][1] = (
            reordered["targets"][1],
            reordered["targets"][0],
        )
        mutations.append(reordered)
        duplicate = json.loads(json.dumps(source))
        duplicate["targets"][1] = dict(duplicate["targets"][0])
        mutations.append(duplicate)
        traversal = json.loads(json.dumps(source))
        traversal["targets"][0]["file"] = "../outside.wav"
        mutations.append(traversal)
        for mutation in mutations:
            with self.subTest(mutation=mutation["targets"][:2]):
                with tempfile.TemporaryDirectory() as directory:
                    copied = Path(directory) / "targets"
                    shutil.copytree(TARGETS, copied)
                    (copied / "manifest.json").write_text(json.dumps(mutation))
                    with self.assertRaises(ExternalTargetError):
                        ExternalTargetBank(copied)


class ExternalTargetControllerTests(unittest.TestCase):
    class FakeRun:
        def __init__(self, target):
            self.external_target = target
            self.status = "IDLE"
            self.history = [{"target_id": target.target_id}]

        def public_status(self):
            return {
                "status": self.status,
                "best_loss": 0.5,
                "target": self.external_target.public_record(),
                "preset_cache": {},
            }

        def history_public(self):
            return list(self.history)

    class FakeController(ExternalTargetController):
        def _new_run(self, target):
            run = ExternalTargetControllerTests.FakeRun(target)
            self._runs.append(run)
            return run

    def controller(self):
        controller = self.FakeController.__new__(self.FakeController)
        controller.target_bank = ExternalTargetBank(TARGETS)
        controller._lock = threading.RLock()
        controller._runs = []
        controller._latest_by_target = {}
        controller.shared_cache = SimpleNamespace(identity_sha256="cache")
        controller.active_run = controller._new_run(controller.target_bank.targets[0])
        return controller

    def test_switch_creates_target_specific_run_without_history_leakage(self) -> None:
        controller = self.controller()
        first_run = controller.active_run
        status = controller.select_target("fm-bell")
        self.assertIsNot(controller.active_run, first_run)
        self.assertEqual(status["selected_target_id"], "fm-bell")
        self.assertEqual(controller.history_public(), [{"target_id": "fm-bell"}])
        self.assertTrue(status["preset_cache"]["reused_for_target_switch"])

    def test_switch_is_rejected_while_search_is_active(self) -> None:
        controller = self.controller()
        controller.active_run.status = "SEARCHING"
        with self.assertRaisesRegex(SurgeMatchError, "stop the active search"):
            controller.select_target("fm-bell")


if __name__ == "__main__":
    unittest.main()
