import os, requests, psycopg2, time

API = os.getenv("APIFOOTBALL_BASE")
KEY = os.getenv("APIFOOTBALL_KEY")
HDR = {"x-apisports-key": KEY}

def upsert_league(cur, row):
    cur.execute("""
        INSERT INTO league_meta(league_id,name,country,logo_url,updated_at)
        VALUES (%s,%s,%s,%s, now())
        ON CONFLICT (league_id)
        DO UPDATE SET name=EXCLUDED.name, country=EXCLUDED.country, logo_url=EXCLUDED.logo_url, updated_at=now();
    """, (row["league"]["id"], row["league"]["name"], row["country"]["name"], row["league"]["logo"]))

def upsert_team(cur, row):
    t = row["team"]
    cur.execute("""
        INSERT INTO team_meta(team_id,name,country,logo_url,updated_at)
        VALUES (%s,%s,%s,%s, now())
        ON CONFLICT (team_id)
        DO UPDATE SET name=EXCLUDED.name, country=EXCLUDED.country, logo_url=EXCLUDED.logo_url, updated_at=now();
    """, (t["id"], t["name"], t.get("country"), t.get("logo")))

def main():
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))  # z.B. postgres://...
    cur = conn.cursor()

    # Aktive Ligen (Beispiel: Top-Ligen + Subset deiner Auswahl)
    leagues = requests.get(f"{API}/leagues?current=true", headers=HDR, timeout=30).json()["response"]
    for L in leagues:
        upsert_league(cur, L)
    conn.commit()

    # Teams je Liga-Saison (hier nur wenige, erweitere nach Bedarf)
    for L in leagues[:50]:
        lid = L["league"]["id"]; sid = L["seasons"][-1]["year"]
        r = requests.get(f"{API}/teams?league={lid}&season={sid}", headers=HDR, timeout=30).json()["response"]
        for t in r:
            upsert_team(cur, t)
        conn.commit()
        time.sleep(0.5)  # höflich bleiben

    cur.close(); conn.close()

if __name__ == "__main__":
    main()
