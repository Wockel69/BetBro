#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
live_stats_detector.py
- Holt aktuelle Live-Spiele
- Prüft pro Liga/Saison die Coverage (statistics_fixtures)
- Testet pro Live-Fixture einmal /fixtures/statistics
- Zeigt 3 Gruppen:
  1) Stats JETZT verfügbar  (response != [])
  2) Liga hat Coverage, aber JETZT leer
  3) Liga ohne Coverage
"""

import os, asyncio, aiohttp, time
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("API_SPORTS_KEY")
BASE = "https://v3.football.api-sports.io"
HDRS = {"x-apisports-key": API_KEY, "Accept": "application/json", "User-Agent": "BetBotLiveStatsDetector/1.0"}

# sanfter Abstand zwischen Calls, um Ratenlimit zu schonen
REQUEST_GAP_SEC = float(os.getenv("REQUEST_GAP_SEC", "0.6"))

if not API_KEY:
    raise SystemExit("API_SPORTS_KEY fehlt in .env")

async def sleep_gap():
    await asyncio.sleep(REQUEST_GAP_SEC)

async def get_json(s: aiohttp.ClientSession, url: str, params=None) -> dict:
    for tries in range(3):
        try:
            async with s.get(url, headers=HDRS, params=params, timeout=40) as r:
                if r.status == 429:
                    await asyncio.sleep(5);  # Backoff bei Rate-Limit
                    continue
                r.raise_for_status()
                return await r.json()
        except aiohttp.ClientError:
            if tries < 2:
                await asyncio.sleep(2)
                continue
            raise

async def league_has_stats(s, league_id: int, season: int) -> bool:
    """Check coverage.fixtures.statistics_fixtures (oder 'statistics' fallback)."""
    data = await get_json(s, f"{BASE}/leagues", {"id": league_id, "season": season})
    resp = data.get("response", [])
    if not resp:
        return False
    seasons = resp[0].get("seasons") or []
    # versuche die exakte Season, sonst letzte
    tgt = next((x for x in seasons if str(x.get("year")) == str(season)), seasons[-1] if seasons else {})
    cov = (tgt.get("coverage") or {}).get("fixtures", {})
    return bool(cov.get("statistics_fixtures") or cov.get("statistics"))

async def fixture_stats_nonempty(s, fixture_id: int) -> bool:
    data = await get_json(s, f"{BASE}/fixtures/statistics", {"fixture": fixture_id})
    return bool(data.get("response"))

async def main():
    async with aiohttp.ClientSession() as s:
        # 1) Live-Fiksturen holen
        fx = await get_json(s, f"{BASE}/fixtures", {"live": "all"})
        lives = fx.get("response", [])
        if not lives:
            print("Keine Live-Spiele gerade.")
            return

        # 2) Coverage je (league, season) bestimmen
        leagues = {}  # (lid, season) -> name
        for row in lives:
            lg = row.get("league") or {}
            lid, season = lg.get("id"), lg.get("season")
            if lid and season:
                leagues[(lid, season)] = lg.get("name")

        coverage = {}
        for (lid, season), name in leagues.items():
            ok = await league_has_stats(s, lid, season)
            coverage[(lid, season)] = ok
            await sleep_gap()

        # 3) Fixtures in drei Gruppen einteilen
        group_now = []     # Stats jetzt verfügbar (response != [])
        group_cov_empty = []  # Liga hat Coverage, aber gerade leer
        group_nocov = []   # Liga ohne Coverage

        for row in lives:
            fx = row.get("fixture") or {}
            lg = row.get("league") or {}
            teams = row.get("teams") or {}
            fid = fx.get("id")
            lid, season = lg.get("id"), lg.get("season")
            minute = (fx.get("status") or {}).get("elapsed")
            home = (teams.get("home") or {}).get("name")
            away = (teams.get("away") or {}).get("name")
            item = (fid, f"{lg.get('name')} {season}", f"{home}–{away}", minute)

            if not coverage.get((lid, season), False):
                group_nocov.append(item)
                continue

            # Liga hat Coverage => teste tatsächlich
            has_now = await fixture_stats_nonempty(s, fid)
            await sleep_gap()
            if has_now:
                group_now.append(item)
            else:
                group_cov_empty.append(item)

        # 4) Ausgabe
        def fmt(items):
            return "\n".join([f"- fid={fid}  [{comp}]  {match}  {minute or 0}'" for fid, comp, match, minute in items]) or "(keine)"

        print("\n=== Live-Stats JETZT verfügbar (response != []) ===")
        print(fmt(group_now))

        print("\n=== Ligen mit Coverage, aber JETZT leer ===")
        print(fmt(group_cov_empty))

        print("\n=== Ligen OHNE Coverage ===")
        print(fmt(group_nocov))

if __name__ == "__main__":
    asyncio.run(main())
