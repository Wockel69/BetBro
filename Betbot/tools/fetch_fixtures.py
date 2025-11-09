#!/usr/bin/env python3
import os, requests, json, datetime as dt

API_KEY = os.getenv("APIFOOTBALL_KEY", "f8be7402447010e1c3a4b67ee8883e56")
API_BASE = os.getenv("APIFOOTBALL_BASE", "https://v3.football.api-sports.io")

# Datum berechnen: heute, morgen oder +N Tage
target = (dt.date.today()).isoformat()   # ändere z.B. auf +dt.timedelta(days=2) für übermorgen
outfile = f"/var/www/Betbot/fixtures_{target}.json"

print(f"Fetching fixtures for {target}...")
r = requests.get(f"{API_BASE}/fixtures", params={"date": target}, headers={"x-apisports-key": API_KEY}, timeout=60)
r.raise_for_status()

data = r.json()
with open(outfile, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

print(f"✅ Saved {len(data.get('response', []))} fixtures to {outfile}")
x