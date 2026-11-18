"""Commandes de démonstration sans interface graphique ni réseau."""
import argparse
import csv
import logging
import sqlite3
import sys
from pathlib import Path

from .common import InputError
from .geo import analyze_geo
from .safety import STANDARD_GRAVITY, SafetyConfig, analyze_safety

ROOT = Path(__file__).resolve().parents[1]


def parser():
    root = argparse.ArgumentParser(description="POC GML Edge : ZFE et freinage, sans réseau.")
    sub = root.add_subparsers(dest="command", required=True)
    geo = sub.add_parser("geo", help="détection ZFE : Bounding Box puis Ray Casting")
    safety = sub.add_parser("safety", help="CSV accéléromètre vers daily_score.json")
    demo = sub.add_parser("demo", help="exécuter les deux modules sur les données de l'annexe")
    for command in (geo, demo):
        command.add_argument("--polygon", type=Path, default=ROOT / "data/lyon_polygon.json")
        command.add_argument("--gps", type=Path, default=ROOT / "data/truck_gps.json")
    for command in (safety, demo):
        command.add_argument("--csv", type=Path, default=ROOT / "data/accelerometer_data.csv")
        command.add_argument("--output", type=Path, default=ROOT / "runtime/daily_score.json")
        command.add_argument("--db", type=Path, default=ROOT / "runtime/events.sqlite3")
        command.add_argument("--truck-id", default="GML-001")
        threshold = command.add_mutually_exclusive_group()
        threshold.add_argument("--threshold-ms2", type=float, help="seuil SI ; défaut : 2.5 m/s² selon l'annexe 3")
        threshold.add_argument("--threshold-g", type=float, help="interprétation littérale en G ; 2.5 G vaut environ 24.52 m/s²")
        command.add_argument("--alpha", type=float, default=.8, help="coefficient EMA, de 0 exclu à 1 inclus")
        command.add_argument("--noise-floor", type=float, default=.15, help="bande morte en m/s²")
        command.add_argument("--penalty-per-event", type=int, default=10, help="pénalité du score pédagogique")
    return root


def main(argv=None):
    arguments = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout, force=True)
    try:
        if arguments.command == "demo":
            inputs = {path.resolve() for path in (arguments.csv, arguments.polygon, arguments.gps)}
            if arguments.output.resolve() in inputs or arguments.db.resolve() in inputs:
                raise InputError("les sorties ne doivent pas écraser les fichiers d'entrée")
        if arguments.command in ("geo", "demo"):
            analyze_geo(arguments.polygon, arguments.gps)
        if arguments.command in ("safety", "demo"):
            threshold = arguments.threshold_ms2 if arguments.threshold_ms2 is not None else 2.5
            if arguments.threshold_g is not None:
                threshold = arguments.threshold_g * STANDARD_GRAVITY
                logging.getLogger("gml").warning(
                    "[UNITÉS] seuil demandé %.3f G = %.6f m/s² ; le CSV reste en m/s²",
                    arguments.threshold_g, threshold,
                )
            config = SafetyConfig(threshold_ms2=threshold, alpha=arguments.alpha,
                                  noise_floor_ms2=arguments.noise_floor,
                                  penalty_per_event=arguments.penalty_per_event)
            analyze_safety(arguments.csv, arguments.output, arguments.db, arguments.truck_id, config)
        return 0
    except (InputError, OSError, UnicodeError, csv.Error, sqlite3.Error) as exc:
        print(f"[ERREUR CONTRÔLÉE] {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("[ARRÊT] interruption demandée", file=sys.stderr)
        return 130
