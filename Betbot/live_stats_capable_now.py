#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, asyncio, aiohttp
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("API_SPORTS_KEY")
BASE = "https://v3.football.api-sports.io"
HDRS = {"x-apisports-key": API_KEY, "Accept": "application/json", "User-Agent": "BetBot/1.0"}

async def get_json(s, url, params=None):
    async with s.get(url, headers=HDRS, params=params, timeout=40) as r:
        r.raise_for_status()
        return await r.json()

async def has_stats(s, league_id, season):
    d = await get_json(s, f"{BASE}/leagues", {"id": league_id, "season": season})
    resp = d.get("response", [])
    if not resp: return False
    seasons = resp[0].get("seasons") or []
    # nimm passende Season, sonst letzte
    tgt = next((x for x in seasons if str(x.get("year")) == str(season)), seasons[-1] if seasons else {})
    cov = (tgt.get("coverage") or {}).get("fixtures", {})
    return bool(cov.get("statistics_fixtures") or cov.get("statistics"))

async def main():
    if not API_KEY:
        print("API_SPORTS_KEY fehlt in .env"); return
    async with aiohttp.ClientSession() as s:
        fx = await get_json(s, f"{BASE}/fixtures", {"live":"all"})
        lives = fx.get("response", [])
        if not lives:
            print("Keine Live-Spiele gerade."); return

        # Unique Ligen sammeln
        leagues = {}
        for row in lives:
            lg = row.get("league") or {}
            leagues[(lg.get("id"), lg.get("season"))] = lg.get("name")

        # Coverage je Liga prüfen
        cover_ok = {}
        for (lid, season), name in leagues.items():
            ok = await has_stats(s, lid, season)
            cover_ok[(lid, season)] = ok

        # Live Fixtures mit Stats-Coverage listen
        print("Live-Spiele MIT Statistics-Coverage:")
        count = 0
        for row in lives:
            fx = row.get("fixture") or {}
            lg = row.get("league") or {}
            fid = fx.get("id")
            minute = (fx.get("status") or {}).get("elapsed")
            lid, season = lg.get("id"), lg.get("season")
            if cover_ok.get((lid, season)):
                print(f"- fid={fid}  {lg.get('name')} {season}  {minute}'  {row.get('teams',{}).get('home',{}).get('name')}–{row.get('teams',{}).get('away',{}).get('name')}")
                count += 1
        if count == 0:
            print("(Derzeit keine live Spiele mit Stats-Coverage)")

        print("\nTipp: Einzeltest für ein Fixture mit Stats:")
        print("curl -H \"x-apisports-key: YOUR_KEY\" \"https://v3.football.api-sports.io/fixtures/statistics?fixture=<FID>\"")

if __name__ == "__main__":
    asyncio.run(main())
