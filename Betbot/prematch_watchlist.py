#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pre-Match Watchlist v2 (Top-N)
- holt alle Fixtures eines Datums (from/to; Fallback auf ?date=)
- lädt Predictions + Odds je Fixture, mit Retries & sanftem Rate-Limit
- Scores: Favorit / Over / Form / Value
- Kategorien-Tuning
- Ausgabe inkl. lokaler Anstoßzeit (Timezone konfigurierbar)
- schreibt Top-N + Vollmenge in JSON
"""

import os, sys, json, argparse, asyncio, datetime as dt
from typing import Dict, Any, List, Tuple, Optional
import aiohttp
from dotenv import load_dotenv

# Python 3.9+: ZoneInfo für TZ-Conversion
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

# ================== ENV ==================
load_dotenv()
API_KEY = os.getenv("API_SPORTS_KEY")
BASE = "https://v3.football.api-sports.io"
HDRS = {"x-apisports-key": API_KEY}
DEFAULT_TZ = os.getenv("API_TZ", "Europe/Berlin")

# ================== Helpers ==================
def _as_float(x, default: float = 0.0) -> float:
    if x is None: return default
    try:
        return float(str(x).replace("%", ""))
    except:
        return default

def _get_float(path_list, default: float = 0.0) -> float:
    cur = path_list[0]
    for p in path_list[1:]:
        cur = (cur or {}).get(p)
    return _as_float(cur, default)

def _pct_from_block(block: Dict[str, Any], keys: List[str], side: str) -> float:
    for k in keys:
        obj = (block.get(k) or {})
        if isinstance(obj, dict) and side in obj:
            return _as_float(obj.get(side), 0.0)
    return 0.0

def clamp(x, lo=0.0, hi=100.0):
    try:
        x = float(x)
    except:
        return lo
    return max(lo, min(hi, x))

def today_str():
    return dt.date.today().isoformat()

def parse_iso_to_local(iso_str: str, tz_name: str) -> Tuple[str, str]:
    """
    ISO-UTC -> lokale Zeit (HH:MM) + Kurzdatum (YYYY-MM-DD).
    Fällt zurück auf UTC, falls ZoneInfo fehlt.
    """
    try:
        d = dt.datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        if ZoneInfo is not None:
            local = d.astimezone(ZoneInfo(tz_name))
        else:
            local = d.astimezone()  # best-effort
        return local.strftime("%H:%M"), local.date().isoformat()
    except Exception:
        return "??:??", iso_str[:10]

def implied_p(odd) -> float:
    try:
        o = float(odd)
        return 1.0 / o if o > 0 else 0.0
    except:
        return 0.0

def pick_market(bets, names):
    for b in bets:
        if b.get("name") in names:
            return b
    return None

def value_from_1x2(bets, predcore):
    """
    Vergleicht Bookie-Implied (1X2) mit API-Prediction percent (home/draw/away).
    Returned (delta, label@odd) oder None.
    """
    b = pick_market(bets, ["Match Winner","1X2"])
    if not b: return None
    pc = predcore.get("percent") or {}
    best = None
    for v in b.get("values", []):
        label = v.get("value")  # "Home"/"Draw"/"Away" oder "1"/"X"/"2"
        key = "home" if label in ("Home","1") else "draw" if label in ("Draw","X") else "away" if label in ("Away","2") else None
        if not key: continue
        p_model = float(str(pc.get(key,"0")).replace("%",""))/100.0 if pc else 0.0
        p_book  = implied_p(v.get("odd"))
        delta   = p_model - p_book
        if (best is None) or (delta > best[0]):
            best = (delta, f"{label}@{v.get('odd')}")
    return best

def value_from_ou25(bets, over_score, default_line="2.5"):
    """
    Value auf O/U 2.5 via grober Mapping- Heuristik:
    over_score 50→~0.5, 70→~0.67, 80→~0.75 als P(Over).
    Returned (delta, "Over 2.5@1.95") oder None.
    """
    b = pick_market(bets, ["Over/Under","Goals Over/Under","Over/Under 2.5"])
    if not b: return None
    p_over_model = max(0.0, min(1.0, (over_score-50)/40*0.5 + 0.5))
    best = None
    for v in b.get("values", []):
        if v.get("handicap") not in (default_line, None):
            continue
        val = v.get("value")  # "Over" / "Under"
        p_book = implied_p(v.get("odd"))
        delta  = (p_over_model - p_book) if val == "Over" else ((1.0 - p_over_model) - p_book)
        label  = f"{val} {v.get('handicap') or default_line}@{v.get('odd')}"
        if (best is None) or (delta > best[0]):
            best = (delta, label)
    return best

# ================== API ==================
async def get_json(session, url, params=None):
    for attempt in range(3):
        try:
            async with session.get(url, headers=HDRS, params=params, timeout=40) as r:
                r.raise_for_status()
                return await r.json()
        except Exception:
            if attempt == 2:
                raise
            await asyncio.sleep(0.8 * (attempt + 1))

async def fetch_fixtures(session, date_iso: str) -> List[Dict[str, Any]]:
    """Versucht from/to; Fallback auf ?date=. Kein timezone/page Param."""
    from_date = date_iso
    to_date = (dt.date.fromisoformat(date_iso) + dt.timedelta(days=1)).isoformat()

    os.makedirs("storage/debug", exist_ok=True)

    fixtures_raw = []
    try:
        data = await get_json(session, f"{BASE}/fixtures", params={"from": from_date, "to": to_date})
        with open(f"storage/debug/fixtures-fromto-{date_iso}.json","w",encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        fixtures_raw = data.get("response", [])
    except Exception as e:
        print(f"⚠️ from/to error: {e}")

    if not fixtures_raw:
        try:
            data = await get_json(session, f"{BASE}/fixtures", params={"date": date_iso})
            with open(f"storage/debug/fixtures-date-{date_iso}.json","w",encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            fixtures_raw = data.get("response", [])
        except Exception as e:
            print(f"⚠️ date error: {e}")

    out = []
    for row in fixtures_raw:
        fixture = row.get("fixture", {}) or {}
        league  = row.get("league", {}) or {}
        teams   = row.get("teams", {})  or {}
        fid = fixture.get("id")
        if not fid:
            continue
        out.append({
            "fixture_id": fid,
            "date_utc": fixture.get("date"),  # ISO in UTC
            "league_name": league.get("name"),
            "country": league.get("country"),
            "home": (teams.get("home") or {}).get("name"),
            "away": (teams.get("away") or {}).get("name"),
        })
    return out

async def fetch_prediction_for_fixture(session, fid: int) -> Optional[Dict[str, Any]]:
    data = await get_json(session, f"{BASE}/predictions", params={"fixture": fid})
    arr = data.get("response", [])
    return arr[0] if arr else None

async def fetch_odds_for_fixture(session, fid: int):
    data = await get_json(session, f"{BASE}/odds", params={"fixture": fid})
    return data.get("response", [])

def prediction_quality_ok(pred: Dict[str, Any]) -> bool:
    if pred.get("predictions"):
        return True
    comparison = pred.get("comparison", {}) or {}
    return bool(comparison)

# ================== Scoring ==================
def compute_scores(pred: Dict[str, Any]) -> Dict[str, Any]:
    """
    Berechnet Favorit / Over / Form; robust gegenüber API-Varianten.
    """
    comparison = pred.get("comparison", {}) or {}
    predcore   = pred.get("predictions", {}) or pred.get("prediction", {}) or {}
    teams      = pred.get("teams", {}) or {}

    # Favorit (bevorzugt "total"; Fallback percent)
    strength_h = _pct_from_block(comparison, ["total", "strength", "wins_the_game"], "home")
    strength_a = _pct_from_block(comparison, ["total", "strength", "wins_the_game"], "away")
    if strength_h == 0 and strength_a == 0:
        pc = predcore.get("percent") or {}
        strength_h = _as_float(pc.get("home"), 0.0)
        strength_a = _as_float(pc.get("away"), 0.0)
    favorit_score = clamp(abs(strength_h - strength_a), 0, 100)

    # Form (Att > Def leicht)
    att_h = _pct_from_block(comparison, ["attacking", "attack", "att"], "home")
    att_a = _pct_from_block(comparison, ["attacking", "attack", "att"], "away")
    de_h  = _pct_from_block(comparison, ["defensive", "defense", "def"], "home")
    de_a  = _pct_from_block(comparison, ["defensive", "defense", "def"], "away")
    form_score = clamp((att_h + att_a)/2.0 - 0.15*(de_h + de_a), 0, 100)

    # Over (Liga-Durchschnitt)
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    h_gf = _get_float([home, "league", "goals", "for",     "average", "total"], 0.0)
    a_gf = _get_float([away, "league", "goals", "for",     "average", "total"], 0.0)
    h_ga = _get_float([home, "league", "goals", "against", "average", "total"], 0.0)
    a_ga = _get_float([away, "league", "goals", "against", "average", "total"], 0.0)
    exp_goals = max(0.0, (h_gf + a_ga + a_gf + h_ga) / 2.0)

    # map exp_goals → Score (≈2.0→50, 3.2→~80)
    over_score = clamp((exp_goals - 2.0) * 38 + 50, 0, 98)  # leicht eingefangen

    # Under/Over Tendenz aus predictions.under_over
    uo = pred.get("predictions", {}).get("under_over") or pred.get("prediction", {}).get("under_over")
    if isinstance(uo, str):
        if uo.startswith("+"):
            over_score = clamp(over_score + 6, 0, 100)
        elif uo.startswith("-"):
            over_score = clamp(over_score - 6, 0, 100)

    return {
        "favorit_score": round(favorit_score, 1),
        "over_score":    round(over_score, 1),
        "form_score":    round(form_score, 1),
    }

def finalize_total_category(scores: Dict[str, Any]) -> Tuple[float, str]:
    """
    Gesamtpunkt und Kategorie; Value wird separat zugemischt.
    """
    over_s  = scores.get("over_score", 50.0)
    fav_s   = scores.get("favorit_score", 0.0)
    form_s  = scores.get("form_score", 0.0)
    val_s   = scores.get("value_score", 0.0)

    total = 0.40*over_s + 0.30*fav_s + 0.15*form_s + 0.15*val_s

    category = "Balanced"
    if fav_s >= 60 and over_s < 65:
        category = "Favoritenspiel"
    elif over_s >= 70 and form_s >= 45:
        category = "Over-Spiel"
    if total >= 72:
        category = "High-Score Match"

    return round(total, 1), category

# ================== Core ==================
def pretty_row(i, g, tz_name: str):
    ko, ko_date = parse_iso_to_local(g['date_utc'], tz_name)
    val = f" • Value +{g['value_score']}% ({g.get('best_value','')})" if g.get("value_score",0)>0 else ""
    return (
        f"{i}️⃣ {g['home']} – {g['away']} "
        f"({g['country']} • {g['league_name']})\n"
        f"Anstoß {ko} {ko_date} ({tz_name})\n"
        f"Score {g['total_score']} • {g['category']} • "
        f"Favorit {g['favorit_score']} • Over {g['over_score']} • Form {g['form_score']}{val}\n"
        f"Advice: {g.get('advice','')}\n"
    )

async def build_watchlist(date_iso: str, tz_name: str, top_n: int, debug: bool=False) -> Tuple[List[Dict[str,Any]], List[Dict[str,Any]]]:
    timeout = aiohttp.ClientTimeout(total=60)
    connector = aiohttp.TCPConnector(limit=12, ttl_dns_cache=300)  # konservativ (stabil)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        fixtures = await fetch_fixtures(session, date_iso)
        total_f = len(fixtures)
        if total_f == 0:
            print(f"Keine Fixtures am {date_iso}.")
            return [], []

        if debug:
            print(f"Fixtures gefunden: {total_f}")
        os.makedirs("storage/debug/preds", exist_ok=True)

        sem = asyncio.Semaphore(10)
        empty_preds = 0
        ok_preds = 0
        err_preds = 0
        dbg_count = 0

        async def worker(f):
            nonlocal empty_preds, ok_preds, err_preds, dbg_count
            async with sem:
                # Predictions mit Retries
                pred = None
                for attempt in range(3):
                    try:
                        pred = await fetch_prediction_for_fixture(session, f["fixture_id"])
                        if pred:
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(0.35 * (attempt + 1))

                if not pred or not prediction_quality_ok(pred):
                    empty_preds += 1
                    if debug:
                        with open(f"storage/debug/preds/{f['fixture_id']}-NOPRED.json","w",encoding="utf-8") as d:
                            json.dump(pred or {}, d, ensure_ascii=False, indent=2)
                    return None

                # Basis-Scores
                base = compute_scores(pred)
                predcore = pred.get("predictions", {}) or pred.get("prediction", {}) or {}

                # Odds + Value
                try:
                    odds_resp = await fetch_odds_for_fixture(session, f["fixture_id"])
                except Exception:
                    odds_resp = []

                all_bets = []
                for r in odds_resp:
                    for bm in r.get("bookmakers", []):
                        all_bets.extend(bm.get("bets", []))

                # Value aus 1X2 und O/U 2.5
                val_1x2 = value_from_1x2(all_bets, predcore) if all_bets else None
                val_ou  = value_from_ou25(all_bets, base.get("over_score", 50.0)) if all_bets else None

                value_score = 0.0
                best_value_label = ""
                candidates = [v for v in (val_1x2, val_ou) if v]
                if candidates:
                    best_delta, best_label = max(candidates, key=lambda x: x[0])
                    value_score = max(0.0, min(100.0, best_delta * 100.0))
                    best_value_label = best_label

                # Winner/Advice (falls vorhanden)
                winner = (predcore.get("winner") or {})
                advice = predcore.get("advice") or ""

                scores = {
                    **f,
                    **base,
                    "value_score": round(value_score, 1),
                    "best_value": best_value_label,
                    "predicted_winner": winner.get("name"),
                    "advice": advice,
                }
                total, category = finalize_total_category(scores)
                scores["total_score"] = total
                scores["category"] = category

                if debug and dbg_count < 3:
                    with open(f"storage/debug/preds/{f['fixture_id']}-OK.json","w",encoding="utf-8") as d:
                        json.dump(pred, d, ensure_ascii=False, indent=2)
                    dbg_count += 1

                ok_preds += 1
                return scores

        # batches mit Mini-Pause
        tasks = []
        batch_size = 40
        for i in range(0, total_f, batch_size):
            batch = fixtures[i:i+batch_size]
            tasks.extend([asyncio.create_task(worker(f)) for f in batch])
            await asyncio.sleep(0.6)

        results = await asyncio.gather(*tasks)
        items = [r for r in results if r]

        if debug:
            print(f"Mit brauchbarer Prediction: {len(items)}  | Leer/NOPRED: {empty_preds}  | Fehler: {err_preds}")

        # Mindestqualitätsfilter optional (auskommentiert lassen, wenn „alles“ gewünscht)
        # items = [g for g in items if g["over_score"] >= 60 or g["favorit_score"] >= 40]

        items.sort(key=lambda x: x["total_score"], reverse=True)
        return items[:top_n], items

# ================== Main ==================
async def main():
    if not API_KEY:
        print("Fehler: API_SPORTS_KEY fehlt in .env")
        sys.exit(1)

    ap = argparse.ArgumentParser(description="Pre-Match Watchlist v2 (Top-N)")
    ap.add_argument("--date", default=today_str(), help="YYYY-MM-DD oder 'today'")
    ap.add_argument("--tz", default=DEFAULT_TZ, help="Zeitzone für Anzeige (z.B. Europe/Berlin)")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    date_iso = today_str() if args.date in ("today","heute") else args.date

    topN, all_items = await build_watchlist(date_iso, args.tz, args.top, args.debug)

    print(f"📊 TOP {len(topN)} SPIELE {date_iso} ({args.tz})\n")
    for i, g in enumerate(topN, 1):
        print(pretty_row(i, g, args.tz))

    os.makedirs("storage", exist_ok=True)
    outpath = os.path.join("storage", f"watchlist-{date_iso}.json")
    with open(outpath, "w", encoding="utf-8") as f:
        json.dump({"date": date_iso, "tz": args.tz, "top": topN, "all": all_items}, f, ensure_ascii=False, indent=2)
    print(f"Gespeichert: {outpath}")

if __name__ == "__main__":
    asyncio.run(main())
