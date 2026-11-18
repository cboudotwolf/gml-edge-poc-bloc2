import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gml_edge.common import InputError
from gml_edge.safety import STANDARD_GRAVITY, BrakeDetector, SafetyConfig, analyze_safety

ROOT = Path(__file__).resolve().parents[1]


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.output = self.folder / "daily_score.json"
        self.db = self.folder / "events.sqlite3"

    def run_safety(self, path=None, config=None):
        with self.assertLogs("gml", level="INFO"):
            result = analyze_safety(path or ROOT / "data/accelerometer_data.csv", self.output, self.db, config=config)
        return result, json.loads(self.output.read_text())

    def write_csv(self, values):
        path = self.folder / "data.csv"
        path.write_text("timestamp,acc_x,acc_y,acc_z\n" + "\n".join(values) + "\n")
        return path

    def test_annex_event_survives_filter_and_score_is_explicit(self):
        metadata, report = self.run_safety()
        self.assertEqual(metadata["summary"]["harsh_braking_count"], 1)
        self.assertEqual(report["input"]["valid_samples"], 10)
        self.assertEqual(report["input"]["observed_mean_frequency_hz"], 1)
        self.assertTrue(report["scoring"]["not_insurer_certified"])
        day = report["days"][0]
        self.assertEqual(day["score"], 90)
        event = day["events"][0]
        self.assertEqual(event["start_timestamp"], 1678880005)
        self.assertEqual(event["peak_raw_y_ms2"], -3.45)
        self.assertAlmostEqual(event["peak_filtered_y_ms2"], -2.904)

    def test_literal_2_5_g_does_not_invent_a_detection(self):
        _, report = self.run_safety(config=SafetyConfig(threshold_ms2=2.5 * STANDARD_GRAVITY))
        self.assertEqual(report["summary"]["harsh_braking_count"], 0)
        self.assertAlmostEqual(report["method"]["threshold_ms2"], 24.516625)

    def test_gravity_on_z_does_not_trigger_longitudinal_braking(self):
        path = self.write_csv([f"{1000+i},0,0.05,9.81" for i in range(5)])
        _, report = self.run_safety(path)
        self.assertEqual(report["summary"]["harsh_braking_count"], 0)

    def test_threshold_is_strict(self):
        detector = BrakeDetector(SafetyConfig(alpha=1, noise_floor_ms2=0))
        self.assertIsNone(detector.push(1, -2.5))
        self.assertIsNone(detector.finish())

    def test_one_event_for_sustained_braking(self):
        detector = BrakeDetector(SafetyConfig(alpha=1))
        events = []
        for index, value in enumerate([0, -3, -4, -3, -1, 0]):
            result = detector.push(index, value)
            if result:
                events.append(result)
        if (last := detector.finish()) is not None:
            events.append(last)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["peak_raw_y_ms2"], -4)

    def test_two_events_after_recovery(self):
        path = self.write_csv([f"{1000+i},0,{value},9.81" for i, value in enumerate([0, -3, 0, -4, 0])])
        _, report = self.run_safety(path, SafetyConfig(alpha=1))
        self.assertEqual(report["summary"]["harsh_braking_count"], 2)
        self.assertEqual(report["days"][0]["score"], 80)

    def test_no_network_is_required(self):
        with patch("socket.socket", side_effect=AssertionError("réseau interdit")):
            self.run_safety()

    def test_replay_is_idempotent_in_local_database(self):
        first, report = self.run_safety()
        _, second = self.run_safety()
        self.assertEqual(report, second)
        with sqlite3.connect(self.db) as connection:
            count = connection.execute("SELECT COUNT(*) FROM gml_poc_events WHERE run_id=?", (first["run_id"],)).fetchone()[0]
        self.assertEqual(count, 1)

    def test_invalid_rows_are_reported_without_silent_success(self):
        path = self.write_csv(["1000,0,0,9.81", "1001,0,nan,9.81", "1000,0,-3,9.81",
                               "1002,0,-3.5,9.81", "1003,0,0,9.81"])
        _, report = self.run_safety(path)
        self.assertEqual(report["input"]["invalid_rows"], 2)
        self.assertFalse(report["input"]["complete_input"])
        self.assertEqual(report["summary"]["harsh_braking_count"], 1)

    def test_missing_columns_and_empty_input_preserve_previous_report(self):
        self.output.write_text('{"previous": true}')
        for content in ["", "timestamp,acc_y\n1,-3\n", "timestamp,acc_x,acc_y,acc_z\n"]:
            path = self.folder / "bad.csv"
            path.write_text(content)
            with self.assertRaises(InputError):
                analyze_safety(path, self.output, self.db)
            self.assertEqual(json.loads(self.output.read_text()), {"previous": True})

    def test_day_change_separates_events_and_scores(self):
        # 2023-03-15 23:59:59 UTC, puis 2023-03-16 00:00:00 UTC.
        path = self.write_csv(["1678924799,0,-4,9.81", "1678924800,0,-4,9.81", "1678924801,0,0,9.81"])
        _, report = self.run_safety(path, SafetyConfig(alpha=1))
        self.assertEqual(len(report["days"]), 2)
        self.assertEqual([day["harsh_braking_count"] for day in report["days"]], [1, 1])

    def test_invalid_parameters(self):
        for config in [SafetyConfig(alpha=0), SafetyConfig(alpha=1.1), SafetyConfig(threshold_ms2=0),
                       SafetyConfig(threshold_ms2=float("nan")), SafetyConfig(noise_floor_ms2=3),
                       SafetyConfig(release_ratio=1), SafetyConfig(penalty_per_event=-1)]:
            with self.subTest(config=config), self.assertRaises(InputError):
                config.validate()

    def test_output_cannot_overwrite_the_input_or_database(self):
        source = self.write_csv(["1000,0,0,9.81"])
        original = source.read_text()
        for output, database in [(source, self.db), (self.output, source), (self.db, self.db)]:
            with self.subTest(output=output, database=database), self.assertRaises(InputError):
                analyze_safety(source, output, database)
        self.assertEqual(source.read_text(), original)


if __name__ == "__main__":
    unittest.main()
