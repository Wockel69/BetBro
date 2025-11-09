#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from dotenv import load_dotenv
load_dotenv(dotenv_path=".env_gamblebros")

import os, time, json, requests, psycopg2, datetime as dt

API = os.getenv("APIFOOTBALL_BASE", "https://v3.football.api-sports.io")
KEY = os.getenv("APIFOOTBALL_KEY")
HDR = {"x-apisports-key": KEY}
TZ  = dt.timezone(dt.timedelta(hours=+1))

MIN_EDGE_PP    = float(os.getenv("MIN_EDGE_PP", "0.05"))
P_MODEL_BOOST  = float(os.getenv("P_MODEL_BOOST", "0.04"))
P_PRED_BOOST   = float(os.getenv("P_PRED_BOOST",  "0.01"))
DEBUG          = os.getenv("DEBUG", "false").lower() in ("1","true","yes","on")

def overmorrow_str(): return (dt.datetime.now(TZ).date() + dt.timedelta(days=2)).isoformat()

def implied(p): 
    try: p=float(p); return 0.0 if p<=0 else 1.0/p
    except: return 0.0

def ensemble(q, pm, pp): return 0.35*q + 0.40*pm + 0.25*pp

def fetch_odds_by_date(date_iso):
    r = requests.get(f"{API}/odds", params={"date": date_iso}, headers=HDR, timeout=60)
    r.raise_for_status()
    return r.json().get("response", [])

def fetch_fixture_meta(fixture_id: int):
    try:
        r = requests.get(f"{API}/fixtures", params={"id": fixture_id}, headers=HDR, timeout=30)
        r.raise_for_status()
        resp = r.json().get("response", [])
        if not resp: return None, None, None, None
        row = resp[0]
        L = (row.get("league") or {}).get("id")
        H = ((row.get("teams") or {}).get("home") or {}).get("id")
        A = ((row.get("teams") or {}).get("away") or {}).get("id")
        ko_iso = ((row.get("fixture") or {}).get("date") or "").replace("Z","+00:00")
        ko = dt.datetime.fromisoformat(ko_iso)
        return L,H,A,ko
    except Exception as e:
        if DEBUG: print(f"[WARN] fixtures?id={fixture_id} -> {e}")
        return None,None,None,None

def latest_prediction(cur, fixture_id:int):
    cur.execute("""
      SELECT payload FROM provider_predictions
      WHERE fixture_id=%s AND provider='api-football'
      ORDER BY fetched_at DESC LIMIT 1
    """,(fixture_id,))
    row = cur.fetchone()
    return row[0] if row else None

def insert_prediction(cur, fixture_id:int, payload:dict):
    cur.execute("""
      INSERT INTO provider_predictions(fixture_id, provider, payload, fetched_at)
      VALUES (%s,'api-football',%s, now())
    """,(fixture_id, json.dumps(payload)))

def fetch_prediction_api(fixture_id:int):
    r = requests.get(f"{API}/predictions", params={"fixture": fixture_id}, headers=HDR, timeout=60)
    r.raise_for_status()
    resp = r.json().get("response", [])
    return resp[0] if resp else None

def p_from_prediction(payload:dict|None, market:str, selection:str, default_q:float)->float:
    if not payload: return default_q + P_PRED_BOOST
    # sehr konservativ – nur ein kleiner Nudge:
    try:
        pred = payload.get("predictions", {})
        if market=="FT_1X2" and selection=="HOME":
            win = (pred.get("winner") or {})
            comment = (win.get("comment") or "").lower()
            if "win or draw" in comment:
                return min(0.95, max(0.01, default_q + max(P_PRED_BOOST, 0.02)))
    except: pass
    return min(0.95, max(0.01, default_q + P_PRED_BOOST))

def upsert_candidate(cur, c):
    cur.execute("""
      INSERT INTO gb_prematch_candidates
        (fixture_id, day_bucket, kickoff_utc, league_id, home_team_id, away_team_id,
         market, selection, line, best_price, q_implied, p_est, edge_pp, confidence,
         source_flags, status, created_at, updated_at)
      VALUES
        (%(fixture_id)s,'OVERMORROW',%(kickoff_utc)s,%(league_id)s,%(home_team_id)s,%(away_team_id)s,
         %(market)s,%(selection)s,%(line)s,%(best_price)s,%(q_implied)s,%(p_est)s,%(edge_pp)s,%(confidence)s,
         %(source_flags)s,'ACTIVE', now(), now())
      ON CONFLICT (fixture_id, market, selection)
      DO UPDATE SET kickoff_utc=EXCLUDED.kickoff_utc, best_price=EXCLUDED.best_price,
        q_implied=EXCLUDED.q_implied, p_est=EXCLUDED.p_est, edge_pp=EXCLUDED.edge_pp,
        confidence=EXCLUDED.confidence, source_flags=EXCLUDED.source_flags, updated_at=now();
    """, c)

def main():
    mode = os.getenv("OVERMORROW_MODE", "refresh")  # "full" um 09:00, sonst "refresh"
    date_iso = overmorrow_str()
    if DEBUG: print(f"[OVERMORROW] date={date_iso} mode={mode}")

    conn = psycopg2.connect(os.getenv("DATABASE_URL")); cur = conn.cursor()

    # 1) ODDS ziehen
    odds = fetch_odds_by_date(date_iso)
    if DEBUG: print(f"[OVERMORROW] odds records: {len(odds)}")

    fixtures_seen = set()

    written = 0
    for item in odds:
        f = item.get("fixture") or {}; books = item.get("bookmakers") or []
        if not books: continue
        fixture_id = f.get("id"); 
        if not fixture_id: continue
        fixtures_seen.add(fixture_id)

        league = item.get("league") or {}; teams = item.get("teams") or {}
        L = league.get("id")
        H = (teams.get("home") or {}).get("id") if teams else None
        A = (teams.get("away") or {}).get("id") if teams else None
        ko = None
        try: ko = dt.datetime.fromisoformat((f.get("date") or "").replace("Z","+00:00"))
        except: pass

        if not (L and H and A and ko):
            L2,H2,A2,KO2 = fetch_fixture_meta(fixture_id)
            L = L or L2; H = H or H2; A = A or A2; ko = ko or KO2
        if not (H and A and ko): 
            if DEBUG: print(f"[skip missing] fx={fixture_id}")
            continue

        # FT 1X2 HOME – beste Quote
        best_home = None
        for b in books:
            for m in b.get("bets", []):
                if m.get("name") in ("Match Winner","Fulltime Result"):
                    for v in m.get("values", []):
                        if v.get("value") in ("Home","1"):
                            try:
                                price = float(v["odd"])
                                best_home = max(best_home, price) if best_home else price
                            except: pass
        if not best_home: continue

        q = implied(best_home)
        # Prediction aus Cache (falls vorhanden)
        pred_payload = latest_prediction(cur, fixture_id)
        p_pred = p_from_prediction(pred_payload, "FT_1X2", "HOME", q)
        p_model = min(0.95, max(0.01, q + P_MODEL_BOOST))
        p = ensemble(q, p_model, p_pred)
        edge = p - q
        if DEBUG: print(f"[calc] fx={fixture_id} q={q:.3f} p_model={p_model:.3f} p_pred={p_pred:.3f} p={p:.3f} edge={edge:.3f}")

        if edge < MIN_EDGE_PP: continue

        cand = dict(
            fixture_id=fixture_id, kickoff_utc=ko, league_id=L, home_team_id=H, away_team_id=A,
            market="FT_1X2", selection="HOME", line=None, best_price=best_home,
            q_implied=round(q,4), p_est=round(p,4), edge_pp=round(edge,4),
            confidence=0.70, source_flags=['MARKET','MODEL'] + (['PREDICTIONS'] if pred_payload else [])
        )
        upsert_candidate(cur, cand)
        written += 1

    conn.commit()
    if DEBUG: print(f"[OVERMORROW] written={written}")

    # 2) Bei "full": Predictions für alle gesehenen Fixtures 1×/Tag cachen
    if mode == "full" and fixtures_seen:
        fetched = 0
        for fx in fixtures_seen:
            cur.execute("""
              SELECT 1 FROM provider_predictions
              WHERE fixture_id=%s AND provider='api-football'
                AND fetched_at::date = now()::date
              LIMIT 1
            """,(fx,))
            if cur.fetchone(): continue
            try:
                payload = fetch_prediction_api(fx)
                if payload:
                    insert_prediction(cur, fx, payload)
                    fetched += 1
                    time.sleep(0.2)
            except Exception as e:
                if DEBUG: print(f"[WARN] predictions fx={fx} -> {e}")
        conn.commit()
        if DEBUG: print(f"[OVERMORROW] predictions fetched={fetched}")

    cur.close(); conn.close()

if __name__ == "__main__":
    main()
