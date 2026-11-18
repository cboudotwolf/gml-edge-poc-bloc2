"""Validation des entrées : les erreurs attendues sont présentées sans traceback."""
import json
import math
from pathlib import Path


class InputError(Exception):
    """Donnée absente, invalide ou hors du périmètre du POC."""


def number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError(f"{name} doit être un nombre JSON")
    value = float(value)
    if not math.isfinite(value):
        raise InputError(f"{name} doit être un nombre fini")
    return value


def load_json_array(path: Path, max_bytes=1024 * 1024):
    # Les JSON de l'annexe sont petits. Une limite explicite évite de charger
    # accidentellement une cartographie continentale ou une très longue trace.
    with path.open("rb") as stream:
        content = stream.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise InputError(f"{path.name} dépasse la limite du POC : {max_bytes} octets")
    try:
        result = json.loads(content.decode("utf-8-sig"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InputError(f"JSON invalide dans {path.name} : {exc}") from exc
    if not isinstance(result, list):
        raise InputError(f"{path.name} doit contenir un tableau JSON")
    return result
