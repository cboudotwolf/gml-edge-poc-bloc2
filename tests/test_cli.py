import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def call(self, *arguments):
        return subprocess.run([sys.executable, str(ROOT / "main.py"), *arguments],
                              cwd=ROOT, text=True, capture_output=True)

    def test_demo_creates_expected_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "daily_score.json"
            result = self.call("demo", "--output", str(output), "--db", str(Path(directory) / "events.sqlite3"))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("[ALERT ZFE] entrée point=4", result.stdout)
            self.assertIn("[FREINAGE VIOLENT]", result.stdout)
            self.assertEqual(json.loads(output.read_text())["summary"]["harsh_braking_count"], 1)

    def test_missing_file_has_controlled_error(self):
        result = self.call("geo", "--gps", "absent.json")
        self.assertEqual(result.returncode, 2)
        self.assertIn("[ERREUR CONTRÔLÉE]", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_malformed_json_has_controlled_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text("{broken")
            result = self.call("geo", "--polygon", str(path))
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)

    def test_invalid_threshold_has_controlled_error(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.call("safety", "--threshold-ms2", "nan", "--db", str(Path(directory) / "db.sqlite3"))
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)

    def test_literal_g_option(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "daily_score.json"
            result = self.call("safety", "--threshold-g", "2.5", "--output", str(path),
                               "--db", str(Path(directory) / "events.sqlite3"))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("[UNITÉS]", result.stdout)
            self.assertEqual(json.loads(path.read_text())["summary"]["harsh_braking_count"], 0)

    def test_demo_does_not_overwrite_geo_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "polygon.json"
            source.write_text((ROOT / "data/lyon_polygon.json").read_text())
            original = source.read_text()
            result = self.call("demo", "--polygon", str(source), "--output", str(source))
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(source.read_text(), original)


if __name__ == "__main__":
    unittest.main()
