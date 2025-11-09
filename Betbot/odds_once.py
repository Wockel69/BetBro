#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, json, aiohttp, asyncio
from dotenv import load_dotenv

# === Lade API Key aus .env ===
load_dotenv()
API_KEY = os.getenv("API_SPORTS_KEY")
if not API_KEY:
    raise SystemExit("API_SPORTS_KEY fehlt in .env")

BASE = "https://v3.football.api-sports.io"
HEADERS = {
    "x-apisports-key": API_KEY,
    "Accept": "application/json",
    "User-Agent": "BetBotOneShot/1.0"
}

async def main():
    async with aiohttp.ClientSession(headers=HEADERS) as s:
        async with s.get(f"{BASE}/odds/live") as r:
            print(f"Status: {r.status}")
            if r.status != 200:
                print(await r.text())
                return
            data = await r.json()

    # Speichern
    out_file = "odds_live_dump.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"✅ Gespeichert als {out_file} ({len(data.get('response', []))} Spiele)")

asyncio.run(main())
