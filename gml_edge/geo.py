"""Bounding Box puis Ray Casting, avec traitement explicite des bordures."""
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from .common import InputError, load_json_array, number

LOG = logging.getLogger("gml")
EPSILON = 1e-10  # Tolérance numérique en degrés, pas une précision GPS garantie.


@dataclass(frozen=True)
class Point:
    lat: float
    lon: float

    @classmethod
    def from_record(cls, value):
        if not isinstance(value, dict):
            raise InputError("une position doit être un objet avec lat et lon")
        try:
            lat = number(value["lat"], "lat")
            lon = number(value["lon"], "lon")
        except KeyError as exc:
            raise InputError(f"coordonnée manquante : {exc.args[0]}") from exc
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise InputError("latitude ou longitude hors limites")
        return cls(lat, lon)


@dataclass(frozen=True)
class BoundingBox:
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float

    def contains(self, point):
        return (
            self.min_lat - EPSILON <= point.lat <= self.max_lat + EPSILON
            and self.min_lon - EPSILON <= point.lon <= self.max_lon + EPSILON
        )


class Polygon:
    def __init__(self, records):
        points = [Point.from_record(value) for value in records]
        if len(points) > 1 and points[0] == points[-1]:
            points.pop()  # La fermeture est assurée par l'itération sur les arêtes.
        if len(set(points)) < 3:
            raise InputError("un polygone doit avoir au moins trois sommets distincts")
        self.points = tuple(points)
        if any(a == b for a, b in self.edges()):
            raise InputError("le polygone contient une arête de longueur nulle")
        # Coordonnées recentrées pour limiter l'erreur d'arrondi du calcul d'aire.
        origin = points[0]
        double_area = sum(
            (a.lon - origin.lon) * (b.lat - origin.lat)
            - (b.lon - origin.lon) * (a.lat - origin.lat)
            for a, b in self.edges()
        )
        if abs(double_area) < EPSILON * EPSILON:
            raise InputError("polygone dégénéré : aire nulle")
        self.bounds = BoundingBox(
            min(p.lat for p in points), max(p.lat for p in points),
            min(p.lon for p in points), max(p.lon for p in points),
        )

    @classmethod
    def from_file(cls, path: Path):
        return cls(load_json_array(path))

    def edges(self):
        for index, point in enumerate(self.points):
            yield point, self.points[(index + 1) % len(self.points)]

    @staticmethod
    def on_segment(point, a, b):
        dx, dy = b.lon - a.lon, b.lat - a.lat
        cross = (point.lon - a.lon) * dy - (point.lat - a.lat) * dx
        tolerance = EPSILON * max(abs(dx), abs(dy), 1.0)
        return (
            abs(cross) <= tolerance
            and min(a.lon, b.lon) - EPSILON <= point.lon <= max(a.lon, b.lon) + EPSILON
            and min(a.lat, b.lat) - EPSILON <= point.lat <= max(a.lat, b.lat) + EPSILON
        )

    def classify(self, point):
        if not self.bounds.contains(point):
            return "OUT"
        return self._ray_cast(point)

    def _ray_cast(self, point):
        inside = False
        for a, b in self.edges():
            if self.on_segment(point, a, b):
                return "BOUNDARY"  # Convention conservatrice : bordure incluse.
            # Une arête horizontale ne satisfait pas cette condition : pas de /0.
            if (a.lat > point.lat) != (b.lat > point.lat):
                intersection_lon = a.lon + (
                    (point.lat - a.lat) * (b.lon - a.lon) / (b.lat - a.lat)
                )
                if point.lon < intersection_lon:
                    inside = not inside
        return "IN" if inside else "OUT"


@dataclass
class GeoSummary:
    valid_points: int = 0
    invalid_points: int = 0
    bbox_rejections: int = 0
    entry_alerts: int = 0
    annotation_mismatches: int = 0


def analyze_geo(polygon_path: Path, gps_path: Path):
    polygon = Polygon.from_file(polygon_path)
    records = load_json_array(gps_path)
    summary = GeoSummary()
    last_reliable_inside = False
    for index, record in enumerate(records, start=1):
        try:
            point = Point.from_record(record)
            timestamp = record.get("timestamp")
            if not isinstance(timestamp, str) or not timestamp.strip():
                raise InputError("timestamp GPS manquant ou invalide")
        except InputError as exc:
            summary.invalid_points += 1
            LOG.warning("[GPS INVALIDE] ligne=%s : %s ; position inconnue", index, exc)
            # Une position invalide ne prouve jamais la sortie d'une ZFE.
            continue
        identifier = record.get("id", index)
        summary.valid_points += 1
        if not polygon.bounds.contains(point):
            summary.bbox_rejections += 1
        state = polygon.classify(point)
        inside = state != "OUT"
        LOG.info("[GPS] point=%s heure=%s position=%s", identifier, timestamp, state)
        if inside and not last_reliable_inside:
            summary.entry_alerts += 1
            LOG.warning("[ALERT ZFE] entrée point=%s heure=%s position=%s", identifier, timestamp, state)
        elif not inside and last_reliable_inside:
            LOG.info("[SORTIE ZFE] point=%s heure=%s", identifier, timestamp)
        last_reliable_inside = inside
        # expected est une annotation de contrôle, jamais une entrée du calcul.
        expected = record.get("expected", "")
        if isinstance(expected, str) and expected.upper().startswith(("IN", "OUT")):
            expected_inside = expected.upper().startswith("IN")
            if expected_inside != inside:
                summary.annotation_mismatches += 1
                LOG.warning(
                    "[ANNOTATION] point=%s : annexe=%s ; géométrie=%s. Les coordonnées sont conservées.",
                    identifier, expected, state,
                )
    if summary.valid_points == 0:
        raise InputError("aucune position GPS exploitable")
    LOG.info("[RÉSUMÉ GEO] %s", asdict(summary))
    return summary
