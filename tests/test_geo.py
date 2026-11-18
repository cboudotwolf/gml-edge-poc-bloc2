import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gml_edge.common import InputError, load_json_array
from gml_edge.geo import Point, Polygon, analyze_geo

ROOT = Path(__file__).resolve().parents[1]


class GeoTests(unittest.TestCase):
    def setUp(self):
        self.polygon = Polygon.from_file(ROOT / "data/lyon_polygon.json")

    def test_original_coordinates_take_precedence_over_expected_annotations(self):
        records = load_json_array(ROOT / "data/truck_gps.json")
        self.assertEqual([self.polygon.classify(Point.from_record(p)) for p in records],
                         ["OUT", "OUT", "OUT", "IN", "IN"])
        with self.assertLogs("gml", level="INFO") as logs:
            result = analyze_geo(ROOT / "data/lyon_polygon.json", ROOT / "data/truck_gps.json")
        self.assertEqual(result.entry_alerts, 1)
        self.assertEqual(result.annotation_mismatches, 1)
        self.assertTrue(any("[ALERT ZFE] entrée point=4" in item for item in logs.output))
        self.assertFalse(any("[ALERT ZFE] entrée point=3" in item for item in logs.output))

    def test_correct_boundary_fixture(self):
        point = Point(45.785, 4.83)
        self.assertEqual(self.polygon.classify(point), "BOUNDARY")
        with self.assertLogs("gml", level="INFO"):
            summary = analyze_geo(ROOT / "data/lyon_polygon.json", ROOT / "data/truck_gps_boundary.json")
        self.assertEqual(summary.entry_alerts, 1)
        self.assertEqual(summary.annotation_mismatches, 0)

    def test_bbox_rejects_without_ray_cast(self):
        with patch.object(self.polygon, "_ray_cast", side_effect=AssertionError("ray casting inutile")):
            self.assertEqual(self.polygon.classify(Point(46.0, 5.0)), "OUT")

    def test_all_vertices_are_boundary(self):
        for point in self.polygon.points:
            self.assertEqual(self.polygon.classify(point), "BOUNDARY")

    def test_open_and_closed_rings_give_same_result(self):
        points = load_json_array(ROOT / "data/lyon_polygon.json")
        open_ring = Polygon(points[:-1])
        self.assertEqual(self.polygon.points, open_ring.points)

    def test_winding_direction_does_not_change_classification(self):
        points = load_json_array(ROOT / "data/lyon_polygon.json")
        reverse = Polygon(list(reversed(points)))
        for point in [Point(45.76, 4.8357), Point(45.785, 4.805), Point(45.785, 4.83)]:
            self.assertEqual(reverse.classify(point), self.polygon.classify(point))

    def test_concavity_and_horizontal_edges(self):
        ring = Polygon([{"lat": lat, "lon": lon} for lat, lon in
                        [(0, 0), (0, 3), (1, 3), (1, 1), (3, 1), (3, 0)]])
        self.assertEqual(ring.classify(Point(2, 2)), "OUT")
        self.assertEqual(ring.classify(Point(.5, 2)), "IN")
        self.assertEqual(ring.classify(Point(0, 2)), "BOUNDARY")

    def test_degenerate_polygon_is_rejected(self):
        with self.assertRaises(InputError):
            Polygon([{"lat": 0, "lon": 0}, {"lat": 1, "lon": 1}, {"lat": 2, "lon": 2}])

    def test_invalid_coordinates(self):
        for value in [None, {}, {"lat": True, "lon": 1}, {"lat": float("nan"), "lon": 1},
                      {"lat": 91, "lon": 1}, {"lat": 1, "lon": 181}, {"lat": "1", "lon": 1}]:
            with self.subTest(value=value), self.assertRaises(InputError):
                Point.from_record(value)

    def test_invalid_json_and_size_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            for content in ["not json", "{}"]:
                path.write_text(content)
                with self.assertRaises(InputError):
                    load_json_array(path)
            path.write_text('["long data"]')
            with self.assertRaises(InputError):
                load_json_array(path, max_bytes=3)

    def test_invalid_point_does_not_fake_exit_or_duplicate_alert(self):
        records = [
            {"id": 1, "timestamp": "08:00:00", "lat": 45.76, "lon": 4.8357},
            {"id": 2, "timestamp": "08:00:01", "lat": None, "lon": 4.8357},
            {"id": 3, "timestamp": "08:00:02", "lat": 45.76, "lon": 4.8357},
            {"id": 4, "timestamp": "08:00:03", "lat": 46, "lon": 5},
            {"id": 5, "timestamp": "08:00:04", "lat": 45.76, "lon": 4.8357},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gps.json"
            path.write_text(json.dumps(records))
            with self.assertLogs("gml", level="INFO"):
                summary = analyze_geo(ROOT / "data/lyon_polygon.json", path)
        self.assertEqual(summary.entry_alerts, 2)
        self.assertEqual(summary.invalid_points, 1)


if __name__ == "__main__":
    unittest.main()
