# normalizers_statistics.py
from __future__ import annotations
from typing import Any, Dict, List

# API liefert je Eintrag z.B.:
# {
#   "team": {"id": 463, "name": "...", "logo": "..."},
#   "statistics": [{"type": "Shots on Goal", "value": 4}, ...]
# }

# Mapping API-Label -> interne Spalte (snake_case)
STAT_KEY_MAP = {
    "Shots on Goal": "shots_on_goal",
    "Shots off Goal": "shots_off_goal",
    "Shots insidebox": "shots_in_box",
    "Shots outsidebox": "shots_outside_box",
    "Total Shots": "shots_total",
    "Blocked Shots": "shots_blocked",
    "Fouls": "fouls",
    "Corner Kicks": "corners",
    "Offsides": "offsides",
    "Ball Possession": "possession_pct",
    "Yellow Cards": "yellow_cards",
    "Red Cards": "red_cards",
    "Goalkeeper Saves": "gk_saves",
    "Total passes": "passes_total",
    "Passes accurate": "passes_accurate",
    "Passes %": "passes_accuracy_pct",
}

def _as_number(x, default=0):
    if x is None:
        return default
    # Werte kommen teils als "62%" oder "14"
    s = str(x).strip()
    if s.endswith("%"):
        s = s[:-1]
    try:
        # erst int versuchen (typisch), sonst float
        i = int(s)
        return i
    except:
        try:
            return float(s)
        except:
            return default

def _empty_row() -> Dict[str, Any]:
    return {
        # Grundinfo
        "fixture_id": None,
        "team_id": None,
        "team_name": None,

        # Metriken (alle vordefiniert -> immer gleiche Spalten)
        "shots_on_goal": 0,
        "shots_off_goal": 0,
        "shots_in_box": 0,
        "shots_outside_box": 0,
        "shots_total": 0,
        "shots_blocked": 0,
        "fouls": 0,
        "corners": 0,
        "offsides": 0,
        "possession_pct": 0.0,
        "yellow_cards": 0,
        "red_cards": 0,
        "gk_saves": 0,
        "passes_total": 0,
        "passes_accurate": 0,
        "passes_accuracy_pct": 0.0,
    }

def normalize_statistics_response(resp: Dict[str, Any], fixture_id: int | None = None) -> List[Dict[str, Any]]:
    """
    Nimmt das gesamte JSON vom Endpoint /fixtures/statistics (response: [ ... ]) und
    gibt eine Liste mit genau 0..2 normalisierten Team-Objekten zurück (home & away).
    """
    rows: List[Dict[str, Any]] = []
    for team_block in resp.get("response", []) or []:
        team = (team_block.get("team") or {})
        stats = (team_block.get("statistics") or [])

        row = _empty_row()
        row["fixture_id"] = fixture_id
        row["team_id"] = team.get("id")
        row["team_name"] = team.get("name")

        # alle bekannten Felder befüllen
        for entry in stats:
            label = entry.get("type")
            if not label:
                continue
            key = STAT_KEY_MAP.get(label)
            if not key:
                # unbekanntes Feld? ignorieren – oder hier optional loggen
                continue
            row[key] = _as_number(entry.get("value"), row[key])

        rows.append(row)

    return rows
