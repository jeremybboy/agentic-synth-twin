"""Local browser calibration and SQLite persistence checks."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from agentic_synth_twin.calibration import (
    CLAP_PARAM_IS_STEPPED,
    CalibrationError,
    CalibrationStore,
    create_calibration_server,
    default_probe_plan,
    render_calibration_page,
)
from agentic_synth_twin.synth_state import load_canonical_state


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "docs" / "evidence" / "milestone-2-canonical-state.json"
PROBE = ROOT / "docs" / "evidence" / "milestone-4-unison-count" / "ab-probe.json"
RESULTS = ROOT / "docs" / "evidence" / "milestone-4-calibration-results.json"


def post_json(url: str, value: dict):
    request = urllib.request.Request(
        url,
        data=json.dumps(value).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, json.load(response)


class CalibrationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "calibration.sqlite3"
        self.store = CalibrationStore(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_default_plan_uses_only_discovered_range_endpoints(self) -> None:
        state = load_canonical_state(CANONICAL)
        inventory = {parameter["id"]: parameter for parameter in state["parameters"]}
        plan = default_probe_plan(state)
        self.assertEqual(len(plan), len(inventory))
        for item in plan:
            parameter = inventory[item["parameter_id"]]
            if parameter["current"] != parameter["max"]:
                expected = parameter["max"]
            elif parameter["flags"] & CLAP_PARAM_IS_STEPPED:
                expected = parameter["min"]
            else:
                expected = (parameter["min"] + parameter["max"]) / 2
            self.assertEqual(item["parameter_value"], expected)
        pre_filter_vca = next(
            item for item in plan if item["parameter_id"] == 87612
        )
        self.assertEqual(pre_filter_vca["parameter_value"], 0.5)

    def test_session_is_blinded_persistent_and_advances(self) -> None:
        session_id = self.store.create_session([PROBE, PROBE], seed=42)
        state = self.store.state(session_id)
        self.assertEqual(state["total"], 2)
        self.assertEqual(state["completed"], 0)
        self.assertNotIn("parameter_name", state["probe"])
        probe_id = state["probe"]["id"]
        with self.assertRaisesRegex(CalibrationError, "listen to both"):
            self.store.record_judgment(session_id, probe_id, True)
        self.store.record_listen(session_id, probe_id, "left")
        heard = self.store.record_listen(session_id, probe_id, "right")
        self.assertTrue(heard["probe"]["left_heard"])
        self.assertTrue(heard["probe"]["right_heard"])
        advanced = self.store.record_judgment(session_id, probe_id, True)
        self.assertEqual(advanced["completed"], 1)
        self.assertNotEqual(advanced["probe"]["id"], probe_id)
        with self.assertRaisesRegex(CalibrationError, "after session completion"):
            self.store.export(session_id)
        second_id = advanced["probe"]["id"]
        self.store.record_listen(session_id, second_id, "left")
        self.store.record_listen(session_id, second_id, "right")
        finished = self.store.record_judgment(session_id, second_id, False)
        self.assertEqual(finished["status"], "completed")
        self.assertIsNone(finished["probe"])
        export = self.store.export(session_id)
        first = export["probes"][0]
        self.assertEqual({first["left_role"], first["right_role"]}, {"A", "B"})
        self.assertEqual(first["meaningful_difference"], 1)
        reopened = CalibrationStore(self.database)
        self.assertEqual(reopened.state(session_id)["completed"], 2)

    def test_only_one_active_session_is_allowed(self) -> None:
        self.store.create_session([PROBE], seed=1)
        with self.assertRaisesRegex(CalibrationError, "active calibration"):
            self.store.create_session([PROBE], seed=2)


class CalibrationPageTests(unittest.TestCase):
    def test_page_exposes_blinded_click_workflow(self) -> None:
        page = render_calibration_page()
        self.assertIn("PLAY SOUND 1", page)
        self.assertIn("PLAY SOUND 2", page)
        self.assertIn("YES · DIFFERENT", page)
        self.assertIn("NO · NOT MEANINGFUL", page)
        self.assertIn("DOWNLOAD RESULTS JSON", page)
        self.assertNotIn("Unison Count", page)


class CompletedCalibrationEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.results = json.loads(RESULTS.read_text(encoding="utf-8"))
        cls.state = load_canonical_state(CANONICAL)

    def test_session_is_complete_and_every_probe_was_heard(self) -> None:
        session = self.results["session"]
        probes = self.results["probes"]
        self.assertEqual(
            self.results["schema"], "agentic-synth-twin/calibration-results/v1"
        )
        self.assertEqual(session["status"], "completed")
        self.assertEqual(session["probe_count"], 10)
        self.assertEqual(len(probes), 10)
        self.assertEqual([probe["ordinal"] for probe in probes], list(range(1, 11)))
        for probe in probes:
            self.assertGreaterEqual(probe["left_play_count"], 1)
            self.assertGreaterEqual(probe["right_play_count"], 1)
            self.assertEqual({probe["left_role"], probe["right_role"]}, {"A", "B"})
            self.assertIn(probe["meaningful_difference"], (0, 1))

    def test_results_match_real_inventory_and_declared_plan(self) -> None:
        inventory = {parameter["id"]: parameter for parameter in self.state["parameters"]}
        plan = {
            item["parameter_id"]: item["parameter_value"]
            for item in default_probe_plan(self.state)
        }
        for probe in self.results["probes"]:
            parameter = inventory[probe["parameter_id"]]
            self.assertEqual(probe["parameter_name"], parameter["name"])
            self.assertEqual(probe["baseline_value"], parameter["current"])
            self.assertEqual(probe["variant_value"], plan[parameter["id"]])

    def test_five_parameters_received_yes(self) -> None:
        yes_ids = {
            probe["parameter_id"]
            for probe in self.results["probes"]
            if probe["meaningful_difference"] == 1
        }
        self.assertEqual(yes_ids, {17, 2391, 2874, 14255, 8675309})


class CalibrationHTTPTests(unittest.TestCase):
    def test_server_records_listening_and_judgment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "calibration.sqlite3"
            store = CalibrationStore(database)
            store.create_session([PROBE], seed=7)
            server = create_calibration_server(
                database_path=database, host="127.0.0.1", port=0
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urllib.request.urlopen(base + "/", timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(b"Human A/B probe", response.read())
                with urllib.request.urlopen(base + "/api/session", timeout=5) as response:
                    state = json.load(response)
                probe = state["probe"]
                self.assertNotIn("parameter_name", probe)
                cross_origin = urllib.request.Request(
                    base + "/api/listen",
                    data=json.dumps({"probe_id": probe["id"], "side": "left"}).encode(
                        "utf-8"
                    ),
                    headers={
                        "Content-Type": "application/json",
                        "Origin": "https://example.com",
                    },
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as blocked:
                    urllib.request.urlopen(cross_origin, timeout=5)
                self.assertEqual(blocked.exception.code, 400)
                with urllib.request.urlopen(base + probe["left_url"], timeout=5) as response:
                    self.assertEqual(response.headers.get_content_type(), "audio/wav")
                    self.assertGreater(len(response.read()), 400_000)
                with self.assertRaises(urllib.error.HTTPError) as rejected:
                    post_json(
                        base + "/api/judgment",
                        {"probe_id": probe["id"], "meaningful_difference": True},
                    )
                self.assertEqual(rejected.exception.code, 400)
                post_json(base + "/api/listen", {"probe_id": probe["id"], "side": "left"})
                _, heard = post_json(
                    base + "/api/listen", {"probe_id": probe["id"], "side": "right"}
                )
                self.assertTrue(heard["probe"]["left_heard"])
                self.assertTrue(heard["probe"]["right_heard"])
                _, finished = post_json(
                    base + "/api/judgment",
                    {"probe_id": probe["id"], "meaningful_difference": True},
                )
                self.assertEqual(finished["status"], "completed")
                with urllib.request.urlopen(base + "/api/export", timeout=5) as response:
                    exported = json.load(response)
                self.assertEqual(exported["probes"][0]["meaningful_difference"], 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_server_rejects_non_loopback_binding(self) -> None:
        with self.assertRaisesRegex(CalibrationError, "loopback"):
            create_calibration_server(
                database_path="ignored.sqlite3", host="0.0.0.0", port=0
            )


if __name__ == "__main__":
    unittest.main()
