#!/usr/bin/env python3
"""Essai reproductible d'un flux CSV synthétique ; pas un test de la tablette."""
import argparse
import csv
import logging
import resource
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gml_edge.safety import analyze_safety


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=100000)
    arguments = parser.parse_args()
    if arguments.samples < 10:
        parser.error("au moins 10 échantillons sont nécessaires")
    logging.getLogger("gml").setLevel(logging.ERROR)
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        source = folder / "synthetic.csv"
        with source.open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["timestamp", "acc_x", "acc_y", "acc_z"])
            for index in range(arguments.samples):
                writer.writerow([1678880000 + index / 100, .1, -3.45 if index == arguments.samples // 2 else .05, 9.81])
        start = time.perf_counter()
        report = analyze_safety(source, folder / "daily_score.json", folder / "events.sqlite3")
        elapsed = time.perf_counter() - start
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_mb = peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024
        print(f"Échantillons : {arguments.samples}")
        print(f"Fréquence simulée : {report['input']['observed_mean_frequency_hz']:.2f} Hz")
        print(f"Freinages détectés : {report['summary']['harsh_braking_count']}")
        print(f"Durée sur cette machine : {elapsed:.3f} s")
        print(f"Mémoire maximale du processus : {peak_mb:.1f} Mio")
        print("Mesure sur ordinateur : intégration et performances Android à valider séparément.")


if __name__ == "__main__":
    main()
