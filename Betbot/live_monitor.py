#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
BetBot Live Monitor (API-Football v3)
- Primär: tippbar = hat 1x2-Markt in odds/live (neues Format: response[].odds)
- Fallback: mit SKIP_ODDS=true läuft er auch ohne Odds (nur fixtures/live + Stats)
- Stats: pro Fixture alle STATS_INTERVAL_SEC (Default 120s)
- Odds: global alle ODDS_REFRESH_SEC (Default 120s)
- Zwischen Stats-Requests: Jitter 1–3s
- Rate Control: Minutenbudget + Mindestabstand
- NEU: Teil-Snapshots (wenn nur ein Team geliefert wird, andere Seite = 0)
"""

import os, json, asyncio, time, datetime as dt, random
import aiohttp
from aiohttp import ClientResponseError
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from db_models import SessionLocal, init_db, Fixture, Snapshot, OddsLive, Alert

# ========= ENV =========
load_dotenv()
API_KEY = os.getenv("API_SPORTS_KEY")
BASE = "https://v3.football.api-sports.io"
HDRS = {
    "x-apisports-key": API_KEY or "",
    "Accept": "application/json",
    "User-Agent": "BetBot/1.0 (+https://betbot.local)"
}
TZ_NAME = os.getenv("TZ", "Europe/Berlin")

SKIP_ODDS = (os.getenv("SKIP_ODDS","false").lower() in ("1","true","yes"))

POLL_SECONDS         = int(os.getenv("POLL_SECONDS", "15"))
STATS_INTERVAL_SEC   = int(os.getenv("STATS_INTERVAL_SEC", "120"))
ODDS_REFRESH_SEC     = int(os.getenv("ODDS_REFRESH_SEC", "120"))
FIXTURES_REFRESH_SEC = int(os.getenv("FIXTURES_REFRESH_SEC", "30"))
JITTER_MIN_SEC       = float(os.getenv("JITTER_MIN_SEC", "1.0"))
JITTER_MAX_SEC       = float(os.getenv("JITTER_MAX_SEC", "3.0"))

STATS_MIN_MINUTE     = int(os.getenv("STATS_MIN_MINUTE", "3"))
STATS_MAX_MINUTE     = int(os.getenv("STATS_MAX_MINUTE", "100"))
MAX_FIXTURES_PER_POLL= int(os.getenv("MAX_FIXTURES_PER_POLL", "200"))

GLOBAL_MAX_REQUESTS_PER_MINUTE = int(os.getenv("GLOBAL_MAX_REQUESTS_PER_MINUTE", "90"))
MIN_REQUEST_INTERVAL_SEC       = float(os.getenv("MIN_REQUEST_INTERVAL_SEC", "1.0"))

ACTIVE_START_HOUR = os.getenv("ACTIVE_START_HOUR")
ACTIVE_END_HOUR   = os.getenv("ACTIVE_END_HOUR")
try:
    ACTIVE_START_HOUR = int(ACTIVE_START_HOUR) if ACTIVE_START_HOUR else None
    ACTIVE_END_HOUR   = int(ACTIVE_END_HOUR)   if ACTIVE_END_HOUR   else None
except:
    ACTIVE_START_HOUR = ACTIVE_END_HOUR = None

WINDOW_SHORT_MIN = int(os.getenv("WINDOW_SHORT_MIN","8"))
WINDOW_LONG_MIN  = int(os.getenv("WINDOW_LONG_MIN","20"))

# ========= Helpers =========
def now_utc_str():
    return dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def safe_i(x):
    try: return int(float(x))
    except: return 0

def safe_f(x):
    try: return float(x)
    except: return 0.0

def get_val(stats, key):
    for s in stats or []:
        if s.get("type") == key:
            v = s.get("value")
            if isinstance(v, str) and v.endswith("%"):
                try: return float(v[:-1])
                except: return None
            try: return float(v)
            except: return None
    return None

def in_active_window(now_utc: dt.datetime) -> bool:
    if ACTIVE_START_HOUR is None or ACTIVE_END_HOUR is None:
        return True
    local = now_utc.astimezone()
    h = local.hour
    if ACTIVE_START_HOUR <= ACTIVE_END_HOUR:
        return ACTIVE_START_HOUR <= h < ACTIVE_END_HOUR
    return (h >= ACTIVE_START_HOUR) or (h < ACTIVE_END_HOUR)

# ========= Minute Budget =========
class MinuteBudget:
    def __init__(self, per_minute:int, min_gap:float):
        self.per_minute = per_minute
        self.min_gap = min_gap
        self._minute_start = time.monotonic()
        self._used_minute = 0
        self._last_req = 0.0

    def _reset_if_needed(self):
        if time.monotonic() - self._minute_start >= 60:
            self._minute_start = time.monotonic()
            self._used_minute = 0

    async def acquire(self):
        self._reset_if_needed()
        gap = self.min_gap - (time.monotonic() - self._last_req)
        if gap > 0:
            await asyncio.sleep(gap)
        if self._used_minute >= self.per_minute:
            wait = 60 - (time.monotonic() - self._minute_start)
            if wait > 0:
                print(f"[{now_utc_str()}] Minutenlimit erreicht – warte {wait:.1f}s")
                await asyncio.sleep(wait)
            self._minute_start = time.monotonic()
            self._used_minute = 0
        self._used_minute += 1
        self._last_req = time.monotonic()

    def stats(self):
        self._reset_if_needed()
        return {"min_used": self._used_minute, "min_cap": self.per_minute}

budget = MinuteBudget(GLOBAL_MAX_REQUESTS_PER_MINUTE, MIN_REQUEST_INTERVAL_SEC)

# ========= HTTP =========
async def get_json(session, url, params=None):
    tries = 0
    while True:
        tries += 1
        await budget.acquire()
        try:
            async with session.get(url, headers=HDRS, params=params, timeout=40) as r:
                if r.status == 429:
                    print(f"[{now_utc_str()}] 429 Too Many Requests – sleep 5s")
                    await asyncio.sleep(5)
                    continue
                r.raise_for_status()
                return await r.json()
        except ClientResponseError as e:
            if e.status in (500, 502, 503, 504) and tries < 3:
                print(f"[{now_utc_str()}] Serverfehler {e.status} – retry...")
                await asyncio.sleep(2)
                continue
            raise
        except aiohttp.ClientError as e:
            if tries < 3:
                print(f"[{now_utc_str()}] Netzfehler {e}, retry...")
                await asyncio.sleep(2)
                continue
            raise

# ========= Odds (neues Format) =========
def _is_1x2_market(name: str) -> bool:
    n = (name or "").lower()
    return ("1x2" in n) or ("match winner" in n) or ("match result" in n) or ("full time" in n)

class OddsForbidden(Exception): pass

async def fetch_odds_live(session):
    data = await get_json(session, f"{BASE}/odds/live")
    out = {}
    for row in data.get("response", []):
        fid = (row.get("fixture") or {}).get("id")
        odds = row.get("odds", []) or []
        if not fid or not odds:
            continue
        m1x2 = next((o for o in odds if _is_1x2_market(o.get("name"))), None)
        if not m1x2:
            continue
        vals = m1x2.get("values", []) or []
        book = {"home": None, "draw": None, "away": None}
        for v in vals:
            val = (v.get("value") or "").lower()
            odd = safe_f(v.get("odd"))
            if val in ("home","1"): book["home"] = odd
            if val in ("draw","x"):  book["draw"] = odd
            if val in ("away","2"):  book["away"] = odd
        if any(book.values()):
            out[fid] = book
    return out

# ========= Fixtures / Stats =========
async def fetch_live_fixtures(session):
    data = await get_json(session, f"{BASE}/fixtures", params={"live": "all"})
    out = []
    for row in data.get("response", []):
        fx = row.get("fixture", {}) or {}
        lg = row.get("league", {}) or {}
        tm = row.get("teams", {}) or {}
        out.append({
            "fixture_id": fx.get("id"),
            "minute": (fx.get("status") or {}).get("elapsed") or 0,
            "league_id": lg.get("id"),
            "league_name": lg.get("name"),
            "season": lg.get("season"),
            "home_name": (tm.get("home") or {}).get("name"),
            "away_name": (tm.get("away") or {}).get("name"),
        })
    return [x for x in out if x["fixture_id"]]

async def fetch_stats(session, fid):
    return await get_json(session, f"{BASE}/fixtures/statistics", params={"fixture": fid})

# ========= DB =========
def upsert_fixture(sess: Session, meta):
    obj = sess.get(Fixture, meta["fixture_id"])
    if not obj:
        obj = Fixture(fixture_id=meta["fixture_id"])
    obj.league_id   = meta.get("league_id")
    obj.league_name = meta.get("league_name")
    obj.season      = meta.get("season")
    obj.home_name   = meta.get("home_name")
    obj.away_name   = meta.get("away_name")
    sess.merge(obj)

def _zero_team():
    return {
        "statistics": [
            {"type":"Shots on Goal","value":0},
            {"type":"Total Shots","value":0},
            {"type":"Corner Kicks","value":0},
            {"type":"Ball Possession","value":0},
            {"type":"Goalkeeper Saves","value":0},
        ]
    }

def insert_snapshot(sess: Session, fid, minute, t0, t1):
    snap = Snapshot(
        fixture_id=fid, minute=minute,
        home_sog=safe_i(get_val(t0["statistics"], "Shots on Goal")),
        home_shots=safe_i(get_val(t0["statistics"], "Total Shots")),
        home_corners=safe_i(get_val(t0["statistics"], "Corner Kicks")),
        home_poss=safe_f(get_val(t0["statistics"], "Ball Possession")),
        home_saves=safe_i(get_val(t0["statistics"], "Goalkeeper Saves")),
        away_sog=safe_i(get_val(t1["statistics"], "Shots on Goal")),
        away_shots=safe_i(get_val(t1["statistics"], "Total Shots")),
        away_corners=safe_i(get_val(t1["statistics"], "Corner Kicks")),
        away_poss=safe_f(get_val(t1["statistics"], "Ball Possession")),
        away_saves=safe_i(get_val(t1["statistics"], "Goalkeeper Saves")),
    )
    sess.add(snap)

def insert_odds(sess: Session, fid, book):
    sess.add(OddsLive(
        fixture_id=fid,
        home_ml=book.get("home"),
        draw_ml=book.get("draw"),
        away_ml=book.get("away"),
    ))

# ========= Caches & Scheduler =========
_last_stats_fetch   = {}   # fid -> monotonic timestamp
_last_odds_pull     = 0.0
_last_fixtures_pull = 0.0
_cached_odds        = {}
_cached_fixtures    = []

def stats_due(fid:int, minute:int, now_mono:float) -> bool:
    if minute is None: return False
    if minute < STATS_MIN_MINUTE or minute > STATS_MAX_MINUTE: return False
    last = _last_stats_fetch.get(fid, 0.0)
    return (now_mono - last) >= STATS_INTERVAL_SEC

# ========= Main Loop =========
async def main_loop():
    if not API_KEY:
        print("API_SPORTS_KEY fehlt in .env"); return
    init_db()
    timeout = aiohttp.ClientTimeout(total=50)
    connector = aiohttp.TCPConnector(limit=8, ttl_dns_cache=300)

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as http:
        odds_forbidden = False
        while True:
            try:
                if not in_active_window(dt.datetime.utcnow()):
                    print(f"[{now_utc_str()}] außerhalb Aktiv-Zeit – sleep {POLL_SECONDS}s")
                    await asyncio.sleep(POLL_SECONDS)
                    continue

                global _last_odds_pull, _cached_odds, _last_fixtures_pull, _cached_fixtures
                now_mono = time.monotonic()

                # 1) Odds (wenn erlaubt)
                if not SKIP_ODDS and not odds_forbidden:
                    if now_mono - _last_odds_pull >= ODDS_REFRESH_SEC or not _cached_odds:
                        try:
                            _cached_odds = await fetch_odds_live(http)
                            _last_odds_pull = time.monotonic()
                            print(f"[{now_utc_str()}] Tippbare Spiele (1x2): {_cached_odds and len(_cached_odds) or 0}")
                        except ClientResponseError as e:
                            if e.status == 403:
                                odds_forbidden = True
                                _cached_odds = {}
                                print(f"[{now_utc_str()}] Hinweis: odds/live 403 → Fallback ohne Odds aktiv.")
                            else:
                                raise

                # 2) Fixtures
                if now_mono - _last_fixtures_pull >= FIXTURES_REFRESH_SEC or not _cached_fixtures:
                    all_live = await fetch_live_fixtures(http)
                    if _cached_odds:
                        _cached_fixtures = [f for f in all_live if f["fixture_id"] in _cached_odds][:MAX_FIXTURES_PER_POLL]
                    else:
                        # Fallback: ohne Odds → alle Live-Spiele
                        _cached_fixtures = all_live[:MAX_FIXTURES_PER_POLL]
                    _last_fixtures_pull = time.monotonic()

                lives = _cached_fixtures
                if not lives:
                    print(f"[{now_utc_str()}] keine Live-Spiele – sleep {POLL_SECONDS}s")
                    await asyncio.sleep(POLL_SECONDS)
                    continue

                # 3) Fixtures + Odds in DB
                with SessionLocal() as sess:
                    for fx in lives:
                        upsert_fixture(sess, fx)
                        fid = fx["fixture_id"]
                        if _cached_odds and fid in _cached_odds:
                            insert_odds(sess, fid, _cached_odds[fid])
                    sess.commit()

                # 4) Stats fällig?
                need_stats = []
                for fx in lives:
                    fid = fx["fixture_id"]
                    minute = int(fx.get("minute") or 0)
                    if stats_due(fid, minute, now_mono):
                        need_stats.append((fid, minute))

                # 5) Stats abarbeiten (mit Jitter), Teil-/Leersnapshots zählen
                stats_done = 0
                partial = 0
                empty = 0
                for fid, minute in need_stats:
                    try:
                        d = await fetch_stats(http, fid)
                        resp = d.get("response", []) or []
                        if len(resp) >= 2:
                            t0, t1 = resp[0], resp[1]
                        elif len(resp) == 1:
                            # Teil-Snapshot: eine Seite vorhanden, andere = 0
                            t0, t1 = resp[0], _zero_team()
                            partial += 1
                        else:
                            # leerer Snapshot: beide = 0 (optional) -> wir zählen als empty und überspringen
                            empty += 1
                            await asyncio.sleep(random.uniform(JITTER_MIN_SEC, JITTER_MAX_SEC))
                            continue

                        with SessionLocal() as sess:
                            insert_snapshot(sess, fid, minute, t0, t1)
                            sess.commit()
                        _last_stats_fetch[fid] = time.monotonic()
                        stats_done += 1
                    except Exception as e:
                        print(f"[{now_utc_str()}] Stats-Fehler für {fid}: {e}")
                    await asyncio.sleep(random.uniform(JITTER_MIN_SEC, JITTER_MAX_SEC))

                s = budget.stats()
                print(f"[{now_utc_str()}] Loop OK – req_min {s['min_used']}/{s['min_cap']} | fixtures {len(lives)} | odds_fixtures {len(_cached_odds)} | stats_now {stats_done} (partial {partial}, empty {empty}) | due {len(need_stats)}")
                await asyncio.sleep(POLL_SECONDS)

            except Exception as e:
                print(f"[{now_utc_str()}] Fehler: {e}")
                await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("bye")
