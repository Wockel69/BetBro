#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, asyncio, aiohttp, time
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("API_SPORTS_KEY")
BASE = "https://v3.football.api-sports.io"
HDRS = {"x-apisports-key": API_KEY, "Accept": "application/json", "User-Agent": "BetBotCoverageScan/1.0"}

if not API_KEY:
    raise SystemExit("API_SPORTS_KEY fehlt in .env")

async def get_json(s, url, params=None):
    async with s.get(url, headers=HDRS, params=params, timeout=40) as r:
        r.raise_for_status()
        return await r.json()

async def has_stats_coverage(s, league_id, season):
    data = await get_json(s, f"{BASE}/leagues", {"id": league_id, "season": season})
    resp = data.get("response", [])
    if not resp:
        return False
    seasons = resp[0].get("seasons") or []
    # nimm die Season mit passender Jahreszahl, sonst letzte
    target = next((x for x in seasons if str(x.get("year")) == str(season)), seasons[-1] if seasons else {})
    cov = (target.get("coverage") or {}).get("fixtures", {})
    return bool(cov.get("statistics"))

async def main():
    async with aiohttp.ClientSession() as s:
        # 1) aktuelle Live-Spiele ziehen
        fx = await get_json(s, f"{BASE}/fixtures", {"live": "all"})
        leagues = {}  # (league_id, season) -> league_name
        for row in fx.get("response", []):
            lg = row.get("league") or {}
            lid = lg.get("id"); season = lg.get("season")
            name = lg.get("name")
            if lid and season:
                leagues[(lid, season)] = name

        if not leagues:
            print("Keine Live-Ligen gefunden.")
            return

        print(f"Gefundene Live-Ligen: {len(leagues)}\nPrüfe Coverage (statistics=true)...")

        ok = []
        no = []
        for (lid, season), name in leagues.items():
            try:
                has = await has_stats_coverage(s, lid, season)
            except Exception as e:
                print(f"  {name} ({lid}/{season}): Fehler {e}")
                continue
            mark = "✅" if has else "❌"
            print(f"  {mark} {name}  (league_id={lid}, season={season})")
            (ok if has else no).append((lid, season, name))

        if ok:
            ids = ",".join(str(lid) for (lid, _, _) in ok)
            print("\nVorschlag für .env (nur Ligen mit Stats-Coverage):")
            print(f"WATCH_LEAGUES={ids}")
        else:
            print("\nKeine Liga mit statistics-Coverage gefunden (zum Zeitpunkt des Scans).")

if __name__ == "__main__":
    asyncio.run(main())
