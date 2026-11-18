"""Lecture CSV en flux, débruitage léger, seuil et regroupement des freinages."""
import csv
import hashlib
import json
import logging
import math
import os
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .common import InputError

LOG = logging.getLogger("gml")
STANDARD_GRAVITY = 9.80665


@dataclass(frozen=True)
class SafetyConfig:
    threshold_ms2: float = 2.5
    alpha: float = 0.8
    noise_floor_ms2: float = 0.15
    release_ratio: float = 0.6
    penalty_per_event: int = 10

    def validate(self):
        for name in ("threshold_ms2", "alpha", "noise_floor_ms2", "release_ratio"):
            if not math.isfinite(getattr(self, name)):
                raise InputError(f"paramètre non fini : {name}")
        if self.threshold_ms2 <= 0 or not 0 < self.alpha <= 1:
            raise InputError("le seuil doit être positif et alpha compris entre 0 et 1 exclu/inclus")
        if not 0 <= self.noise_floor_ms2 < self.threshold_ms2:
            raise InputError("la bande morte doit être positive ou nulle et inférieure au seuil")
        if not 0 < self.release_ratio < 1:
            raise InputError("le ratio de relâchement doit être compris strictement entre 0 et 1")
        if not isinstance(self.penalty_per_event, int) or self.penalty_per_event < 0:
            raise InputError("la pénalité de score doit être un entier positif ou nul")


class BrakeDetector:
    def __init__(self, config):
        self.config = config
        self.filtered = 0.0
        self.active = None

    def push(self, timestamp, raw_y):
        clean = 0.0 if abs(raw_y) <= self.config.noise_floor_ms2 else raw_y
        self.filtered = self.config.alpha * clean + (1 - self.config.alpha) * self.filtered
        release = -self.config.threshold_ms2 * self.config.release_ratio
        if self.active is not None and self.filtered >= release:
            return self.finish()
        if self.active is None and self.filtered < -self.config.threshold_ms2:
            self.active = {
                "type": "harsh_braking", "start_timestamp": timestamp,
                "end_timestamp": timestamp, "peak_raw_y_ms2": raw_y,
                "peak_filtered_y_ms2": self.filtered, "samples_in_event": 1,
            }
        elif self.active is not None:
            self.active["end_timestamp"] = timestamp
            self.active["peak_raw_y_ms2"] = min(self.active["peak_raw_y_ms2"], raw_y)
            self.active["peak_filtered_y_ms2"] = min(self.active["peak_filtered_y_ms2"], self.filtered)
            self.active["samples_in_event"] += 1
        return None

    def finish(self):
        event, self.active = self.active, None
        if event is not None:
            event["duration_observed_s"] = event["end_timestamp"] - event["start_timestamp"]
        return event

    def interrupt(self):
        event = self.finish()
        self.filtered = 0.0
        return event


def file_run_id(path, truck_id, config):
    digest = hashlib.sha256()
    digest.update(json.dumps([truck_id, asdict(config), "gml-poc-1"], sort_keys=True).encode())
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sample_from_row(row, previous_timestamp):
    if None in row or any(row.get(key) is None for key in ("timestamp", "acc_x", "acc_y", "acc_z")):
        raise InputError("nombre de colonnes CSV incorrect")
    try:
        timestamp, x, y, z = (float(row[key]) for key in ("timestamp", "acc_x", "acc_y", "acc_z"))
    except (TypeError, ValueError) as exc:
        raise InputError("valeur CSV non numérique") from exc
    if not all(math.isfinite(value) for value in (timestamp, x, y, z)):
        raise InputError("valeur CSV non finie")
    if timestamp < 0 or (previous_timestamp is not None and timestamp <= previous_timestamp):
        raise InputError("timestamps non croissants ou négatifs")
    try:
        day = datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError) as exc:
        raise InputError("timestamp hors limites") from exc
    return timestamp, y, day


def create_schema(connection):
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS gml_poc_days (
            run_id TEXT NOT NULL, day_utc TEXT NOT NULL, valid_samples INTEGER NOT NULL,
            PRIMARY KEY (run_id, day_utc)
        );
        CREATE TABLE IF NOT EXISTS gml_poc_events (
            run_id TEXT NOT NULL, event_id TEXT NOT NULL, day_utc TEXT NOT NULL,
            start_timestamp REAL NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY (run_id, event_id)
        );
        CREATE INDEX IF NOT EXISTS gml_poc_events_by_day
            ON gml_poc_events(run_id, day_utc, start_timestamp);
    """)


def save_event(connection, run_id, truck_id, event):
    if event is None:
        return
    day = datetime.fromtimestamp(event["start_timestamp"], timezone.utc).date().isoformat()
    identity = json.dumps([run_id, truck_id, event["start_timestamp"], event["end_timestamp"]])
    event["event_id"] = hashlib.sha256(identity.encode()).hexdigest()[:20]
    event["truck_id"] = truck_id
    payload = json.dumps(event, ensure_ascii=False, allow_nan=False)
    connection.execute(
        "INSERT OR REPLACE INTO gml_poc_events VALUES (?, ?, ?, ?, ?)",
        (run_id, event["event_id"], day, event["start_timestamp"], payload),
    )
    LOG.warning("[FREINAGE VIOLENT] t=%s brut=%.3f m/s² filtré=%.3f m/s²",
                event["start_timestamp"], event["peak_raw_y_ms2"], event["peak_filtered_y_ms2"])


def write_report(connection, output, metadata, run_id, config):
    """Écriture atomique et en flux : la liste des événements reste sur disque."""
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".daily-score-", suffix=".json", dir=output.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            opening = json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write(opening[:-1] + ',\n  "days": [\n')
            first_day = True
            for day, count in connection.execute(
                "SELECT day_utc, valid_samples FROM gml_poc_days WHERE run_id=? ORDER BY day_utc", (run_id,)
            ):
                event_count = connection.execute(
                    "SELECT COUNT(*) FROM gml_poc_events WHERE run_id=? AND day_utc=?", (run_id, day)
                ).fetchone()[0]
                item = {
                    "date_utc": day, "valid_samples": count,
                    "harsh_braking_count": event_count,
                    "score": max(0, 100 - config.penalty_per_event * event_count),
                }
                if not first_day:
                    stream.write(',\n')
                first_day = False
                stream.write('    ' + json.dumps(item, ensure_ascii=False)[:-1] + ', "events": [')
                first_event = True
                for payload, in connection.execute(
                    "SELECT payload FROM gml_poc_events WHERE run_id=? AND day_utc=? ORDER BY start_timestamp",
                    (run_id, day),
                ):
                    if not first_event:
                        stream.write(', ')
                    first_event = False
                    stream.write(payload)
                stream.write(']}')
            stream.write('\n  ]\n}\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def analyze_safety(csv_path: Path, output: Path, database: Path, truck_id="GML-001", config=None):
    config = config or SafetyConfig()
    config.validate()
    if len({csv_path.resolve(), output.resolve(), database.resolve()}) != 3:
        raise InputError("le CSV source, le rapport et la base SQLite doivent être des fichiers distincts")
    if not truck_id.strip():
        raise InputError("l'identifiant camion ne doit pas être vide")
    run_id = file_run_id(csv_path, truck_id, config)
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    try:
        create_schema(connection)
        detector = BrakeDetector(config)
        valid, invalid = 0, 0
        first_timestamp = last_timestamp = previous_day = None
        with connection:
            # Rejouer exactement le même fichier/configuration ne double pas les événements.
            connection.execute("DELETE FROM gml_poc_events WHERE run_id=?", (run_id,))
            connection.execute("DELETE FROM gml_poc_days WHERE run_id=?", (run_id,))
            with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                required = {"timestamp", "acc_x", "acc_y", "acc_z"}
                if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                    raise InputError("colonnes CSV requises : timestamp,acc_x,acc_y,acc_z")
                if len(reader.fieldnames) != len(set(reader.fieldnames)):
                    raise InputError("en-têtes CSV dupliqués")
                for row in reader:
                    try:
                        timestamp, raw_y, day = sample_from_row(row, last_timestamp)
                    except InputError as exc:
                        invalid += 1
                        save_event(connection, run_id, truck_id, detector.interrupt())
                        LOG.warning("[CSV INVALIDE] ligne=%s : %s ; ligne ignorée", reader.line_num, exc)
                        continue
                    if previous_day is not None and day != previous_day:
                        save_event(connection, run_id, truck_id, detector.interrupt())
                    if first_timestamp is None:
                        first_timestamp = timestamp
                    last_timestamp, previous_day = timestamp, day
                    valid += 1
                    connection.execute(
                        "INSERT INTO gml_poc_days VALUES (?, ?, 1) "
                        "ON CONFLICT(run_id, day_utc) DO UPDATE SET valid_samples=valid_samples+1",
                        (run_id, day),
                    )
                    save_event(connection, run_id, truck_id, detector.push(timestamp, raw_y))
            if not valid:
                raise InputError("aucun échantillon CSV exploitable")
            save_event(connection, run_id, truck_id, detector.finish())
        event_count = connection.execute(
            "SELECT COUNT(*) FROM gml_poc_events WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        days_count = connection.execute(
            "SELECT COUNT(*) FROM gml_poc_days WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        frequency = ((valid - 1) / (last_timestamp - first_timestamp)) if valid > 1 else None
        metadata = {
            "schema_version": "1.0", "truck_id": truck_id, "run_id": run_id,
            "input": {"file": csv_path.name, "unit": "m/s2", "valid_samples": valid,
                      "invalid_rows": invalid, "complete_input": invalid == 0,
                      "first_timestamp": first_timestamp, "last_timestamp": last_timestamp,
                      "observed_mean_frequency_hz": frequency, "target_sensor_frequency_hz": 100},
            "method": {**asdict(config), "longitudinal_axis": "acc_y",
                       "rule": "filtered_acc_y < -threshold_ms2",
                       "release_threshold_ms2": config.threshold_ms2 * config.release_ratio},
            "scoring": {"type": "pedagogical", "not_insurer_certified": True,
                        "formula": f"max(0, 100 - {config.penalty_per_event} * harsh_braking_count)",
                        "coverage": "only the provided samples, not a full driving day"},
            "summary": {"harsh_braking_count": event_count, "days_analyzed": days_count},
        }
        write_report(connection, output, metadata, run_id, config)
        if frequency is not None and abs(frequency - 100) > 10:
            LOG.warning("[CADENCE] fréquence moyenne du fichier : %.2f Hz ; ce jeu ne valide pas un capteur à 100 Hz", frequency)
        LOG.info("[RÉSUMÉ SAFETY] échantillons=%s invalides=%s freinages=%s fichier=%s", valid, invalid, event_count, output)
        return metadata
    finally:
        connection.close()
