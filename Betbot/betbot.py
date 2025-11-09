#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
BetBot – Orchestrator mit API-Football + Fallback AiScoreWorkerPool

Flow:
1) odds/live  -> bestimmt tippbare Fixtures
2) fixtures(live=all) -> Meta/Minute, speichert Fixture + Odds in DB
3) fixtures/statistics -> wenn vorhanden: Snapshot-Insert in DB
4) Fallback: Fehlen Stats -> AiScoreWorkerPool starten (Playwright, headless)
5) Auto-Stop: wenn API-Stats da sind oder Fixture nicht mehr live ist
"""

import os, asyncio, time, json, random, aiohttp
from typing import Dict, Optional, List
from dotenv import load_dotenv
from datetime import datetime, timezone

# DB-Modelle (wie in deinem Projekt)
from db_models import SessionLocal, Fixture, Snapshot, OddsLive

# Dein Worker-Pool (genau die Datei, die du gesendet hast)
from aiscore_worker import AiScoreWorkerPool  # noqa: F401 (wird genutzt)

load_dotenv()

BASE = "https://v3.football.api-sports.io"
API_KEY = os.getenv("API_SPORTS_KEY", "")
if not API_KEY:
    raise SystemExit("Fehlender API_SPORTS_KEY in .env")

HDRS = {
    "x-apisports-key": API_KEY,
    "Accept": "application/json",
    "User-Agent": "BetBot/Unified/2.0",
}

# Intervalle
FIXTURES_REFRESH_SEC = int(os.getenv("FIXTURES_REFRESH_SEC", "30"))
ODDS_REFRESH_SEC     = int(os.getenv("ODDS_REFRESH_SEC", "60"))
STATS_INTERVAL_SEC   = int(os.getenv("STATS_INTERVAL_SEC", "60"))  # API-Stats Poll pro Fixture
MIN_REQUEST_GAP_SEC  = float(os.getenv("MIN_REQUEST_INTERVAL_SEC", "0.8"))

# AiScore Worker Einstellungen (werden in aiscore_worker.py gelesen)
AISO_MAX_PARALLEL    = int(os.getenv("AISO_MAX_PARALLEL", "12"))
AISO_HEADLESS        = os.getenv("AISO_HEADLESS", "true").lower() in ("1","true","yes")
AISO_INTERVAL_SEC    = int(os.getenv("AISO_INTERVAL_SEC", "30"))

def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

async def get_json(session: aiohttp.ClientSession, url: str, **params):
    await asyncio.sleep(MIN_REQUEST_GAP_SEC)
    async with session.get(url, headers=HDRS, params=params or None, timeout=45) as r:
        if r.status == 429:
            # backoff & retry
            await asyncio.sleep(5)
            return await get_json(session, url, **params)
        r.raise_for_status()
        return await r.json()

def is_live_short(s: Optional[str]) -> bool:
    """True = Spiel mutmaßlich live (API-Shortcodes)."""
    if not s:
        return True
    return s not in {"FT", "AET", "PEN", "PST", "CANC", "ABD", "AWD", "WO"}

def _pick_1x2_market(odds_list: list) -> Optional[dict]:
    keys = ("1x2","match result","match winner","full time","winner",
            "regular time","win-draw-win","resultado final","ergebnis (3-weg)")
    for o in odds_list or []:
        name = (o.get("name") or "").lower()
        if any(k in name for k in keys):
            return o
    return None

async def fetch_odds_live(session) -> Dict[int, Dict[str, float]]:
    """odds/live → dict[fid] = {'home','draw','away'} (nur wenn 1X2 existiert)."""
    data = await get_json(session, f"{BASE}/odds/live")
    out: Dict[int, Dict[str, float]] = {}
    for row in data.get("response", []) or []:
        fixture = row.get("fixture") or {}
        fid = fixture.get("id")
        if not fid:
            continue
        m = _pick_1x2_market(row.get("odds") or [])
        if not m:
            continue
        book = {"home": None, "draw": None, "away": None}
        for v in m.get("values", []) or []:
            val = (v.get("value") or "").lower()
            odd = v.get("odd")
            try:
                odd = float(str(odd).replace(",", "."))
            except:
                odd = None
            if val in ("home", "1"): book["home"] = odd
            if val in ("draw", "x"):  book["draw"] = odd
            if val in ("away", "2"):  book["away"] = odd
        if any(book.values()):
            out[fid] = book
    return out

async def fetch_live_fixtures(session) -> Dict[int, dict]:
    """fixtures(live=all) → dict[fid] -> Meta (Minute, Teams, Liga, Status)."""
    data = await get_json(session, f"{BASE}/fixtures", live="all")
    res: Dict[int, dict] = {}
    for r in data.get("response", []) or []:
        fx   = r.get("fixture") or {}
        lg   = r.get("league") or {}
        tms  = r.get("teams") or {}
        fid  = fx.get("id")
        if not fid:
            continue
        res[fid] = {
            "fixture_id": fid,
            "status_short": (fx.get("status") or {}).get("short"),
            "minute": (fx.get("status") or {}).get("elapsed") or 0,
            "league_id": lg.get("id"), "league_name": lg.get("name"), "season": lg.get("season"),
            "home_id": (tms.get("home") or {}).get("id"),
            "home_name": (tms.get("home") or {}).get("name"),
            "away_id": (tms.get("away") or {}).get("id"),
            "away_name": (tms.get("away") or {}).get("name"),
        }
    return res

async def fetch_stats(session, fixture_id: int) -> List[dict]:
    data = await get_json(session, f"{BASE}/fixtures/statistics", fixture=fixture_id)
    return data.get("response", []) or []

def get_stat(stats: list, key: str) -> Optional[float]:
    for s in stats or []:
        if s.get("type") == key:
            v = s.get("value")
            if isinstance(v, str) and v.endswith("%"):
                try: return float(v[:-1])
                except: return None
            try: return float(v)
            except: return None
    return None

# ==== DB Helfer ====
def upsert_fixture(sess, meta: dict):
    f = sess.get(Fixture, meta["fixture_id"])
    if not f:
        f = Fixture(
            fixture_id=meta["fixture_id"],
            league_id=meta.get("league_id"), league_name=meta.get("league_name"), season=meta.get("season"),
            home_id=meta.get("home_id"), home_name=meta.get("home_name"),
            away_id=meta.get("away_id"), away_name=meta.get("away_name"),
        )
        sess.add(f)
    else:
        f.league_id = meta.get("league_id")
        f.league_name = meta.get("league_name")
        f.season = meta.get("season")
        f.home_id = meta.get("home_id"); f.home_name = meta.get("home_name")
        f.away_id = meta.get("away_id"); f.away_name = meta.get("away_name")

def insert_odds(sess, fid: int, book: dict):
    sess.add(OddsLive(
        fixture_id=fid,
        home_ml=book.get("home"),
        draw_ml=book.get("draw"),
        away_ml=book.get("away")
    ))

def insert_snapshot_from_api(sess, fid: int, minute: int, h_stats: list, a_stats: list):
    snap = Snapshot(
        fixture_id=fid, minute=minute,
        home_sog=int(get_stat(h_stats, "Shots on Goal") or 0),
        home_shots=int(get_stat(h_stats, "Total Shots") or 0),
        home_corners=int(get_stat(h_stats, "Corner Kicks") or 0),
        home_saves=int(get_stat(h_stats, "Goalkeeper Saves") or 0),
        home_poss=float(get_stat(h_stats, "Ball Possession") or 0.0),
        away_sog=int(get_stat(a_stats, "Shots on Goal") or 0),
        away_shots=int(get_stat(a_stats, "Total Shots") or 0),
        away_corners=int(get_stat(a_stats, "Corner Kicks") or 0),
        away_saves=int(get_stat(a_stats, "Goalkeeper Saves") or 0),
        away_poss=float(get_stat(a_stats, "Ball Possession") or 0.0),
    )
    sess.add(snap)

# ==== Orchestrator ====
async def run():
    timeout = aiohttp.ClientTimeout(total=50)
    connector = aiohttp.TCPConnector(limit=16, ttl_dns_cache=300)

    last_odds_pull = 0.0
    last_fixtures_pull = 0.0
    cached_odds: Dict[int, dict] = {}
    cached_fx: Dict[int, dict] = {}
    last_stats_req: Dict[int, float] = {}  # anti-burst pro Fixture

    # Diese Sets steuern, wann der Worker gestoppt wird
    api_has_stats: Dict[int, bool] = {}  # wenn True: Worker stoppen
    still_live: Dict[int, bool] = {}

    # Pool nach deiner Worker-Datei
    pool = AiScoreWorkerPool(
        max_parallel=AISO_MAX_PARALLEL,
        scrape_interval=AISO_INTERVAL_SEC,
        headless=AISO_HEADLESS,
        on_insert=_on_insert_from_aiscore(cached_fx),
        should_stop=_should_stop_factory(api_has_stats, still_live),
    )
    await pool.start()

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as http:
        while True:
            try:
                mono = time.monotonic()

                # 1) odds/live
                if mono - last_odds_pull >= ODDS_REFRESH_SEC or not cached_odds:
                    try:
                        cached_odds = await fetch_odds_live(http)
                        print(f"[{ts()}] odds/live: tippbar={len(cached_odds)}")
                    except Exception as e:
                        print(f"[{ts()}] odds/live Fehler: {e}")
                    last_odds_pull = time.monotonic()

                # 2) fixtures live
                if mono - last_fixtures_pull >= FIXTURES_REFRESH_SEC or not cached_fx:
                    all_live = await fetch_live_fixtures(http)
                    cached_fx = {fid: all_live[fid] for fid in all_live if fid in cached_odds}
                    print(f"[{ts()}] fixtures/live: live={len(all_live)} | tippbar={len(cached_fx)}")
                    last_fixtures_pull = time.monotonic()

                    # DB upsert + odds
                    with SessionLocal() as sess:
                        for fid, meta in cached_fx.items():
                            upsert_fixture(sess, meta)
                            if fid in cached_odds:
                                insert_odds(sess, fid, cached_odds[fid])
                        sess.commit()

                    # setze still_live flags, stoppe Worker für nicht-live
                    active_ids = set(cached_fx.keys())
                    for fid in list(still_live.keys()):
                        if fid not in active_ids:
                            still_live[fid] = False
                    for fid in active_ids:
                        still_live[fid] = is_live_short(cached_fx[fid].get("status_short"))

                if not cached_fx:
                    print(f"[{ts()}] keine tippbaren Live-Spiele – sleep {FIXTURES_REFRESH_SEC}s")
                    await asyncio.sleep(FIXTURES_REFRESH_SEC)
                    continue

                # 3) pro tippbarem Fixture: API-Stats Try, sonst Worker
                for fid, meta in list(cached_fx.items()):
                    if mono - last_stats_req.get(fid, 0.0) < STATS_INTERVAL_SEC:
                        continue
                    last_stats_req[fid] = mono

                    try:
                        resp = await fetch_stats(http, fid)
                    except Exception as e:
                        print(f"[{ts()}] stats Fehler {fid}: {e}")
                        resp = []

                    if len(resp) >= 2:
                        # API liefert: Snapshot speichern und (falls läuft) Worker stoppen
                        t0, t1 = resp[0], resp[1]
                        minute = int(meta.get("minute") or 0)
                        with SessionLocal() as sess:
                            insert_snapshot_from_api(sess, fid, minute, t0.get("statistics") or [], t1.get("statistics") or [])
                            sess.commit()
                        api_has_stats[fid] = True
                    else:
                        api_has_stats[fid] = False
                        # Worker starten, wenn nicht bereits aktiv
                        if not pool.is_running(fid):
                            await pool.submit({
                                "match_id": fid,
                                "home": meta.get("home_name","") or "",
                                "away": meta.get("away_name","") or "",
                            })

                    await asyncio.sleep(random.uniform(0.25, 0.7))

                print(f"[{ts()}] Loop ok – tippbar={len(cached_fx)} | workers={pool.count_running()}")
                await asyncio.sleep(1.0)

            except Exception as e:
                print(f"[{ts()}] Main-Fehler: {e}")
                await asyncio.sleep(3)

# ==== Callbacks & Stop-Logic ====
def _on_insert_from_aiscore(cached_fx_ref: Dict[int, dict]):
    """
    Callback für AiScoreWorkerPool.on_insert(row).
    Mappt AiScore-Snapshot -> deine Snapshot-Tabelle (Basisfelder).
    """
    async def _on_insert(row: Dict):
        fid = row.get("match_id")
        minute = row.get("minute") or 0
        # Mappe nur robuste Felder in existierende Spalten:
        home_shots = int(row.get("shots_h") or 0)
        away_shots = int(row.get("shots_a") or 0)
        home_sog   = int(row.get("sog_h") or 0)
        away_sog   = int(row.get("sog_a") or 0)
        home_corn  = int(row.get("corners_h") or 0)
        away_corn  = int(row.get("corners_a") or 0)
        home_poss  = float(row.get("possession_h") or 0.0)
        away_poss  = float(row.get("possession_a") or 0.0)

        with SessionLocal() as sess:
            sess.add(Snapshot(
                fixture_id=fid, minute=int(minute),
                home_sog=home_sog, home_shots=home_shots, home_corners=home_corn, home_saves=0, home_poss=home_poss,
                away_sog=away_sog, away_shots=away_shots, away_corners=away_corn, away_saves=0, away_poss=away_poss
            ))
            sess.commit()
        print(f"[{ts()}] [AiScore→DB] {fid} min={minute} SH={home_shots}-{away_shots} SOG={home_sog}-{away_sog} CORN={home_corn}-{away_corn} POS={home_poss}-{away_poss}")
    return _on_insert

def _should_stop_factory(api_has_stats: Dict[int, bool], still_live: Dict[int, bool]):
    """
    Stoppt Worker, wenn:
    - API bereits Stats liefert (api_has_stats[fid] == True)
    - Fixture nicht mehr live (still_live.get(fid) == False)
    Der Worker selbst stoppt zusätzlich bei „Ended/FT“ (DOM), siehe aiscore_worker.py.
    """
    async def _should_stop(task: Dict[str, any]) -> bool:
        fid = task.get("match_id")
        if fid is None:
            return False
        if api_has_stats.get(fid, False):
            return True
        live = still_live.get(fid, True)
        if not live:
            return True
        return False
    return _should_stop

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("bye")
