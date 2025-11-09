#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from dotenv import load_dotenv
load_dotenv(dotenv_path=".env_gamblebros")

import os, requests, psycopg2, datetime as dt

API = os.getenv("APIFOOTBALL_BASE", "https://v3.football.api-sports.io")
KEY = os.getenv("APIFOOTBALL_KEY")
HDR = {"x-apisports-key": KEY}
TZ  = dt.timezone(dt.timedelta(hours=+1))  # Berlin (Winter) – passe ggf. für Sommerzeit an

# --- Laufzeit-Parameter aus ENV (konfigurierbar) ------------------------------
MIN_EDGE_PP    = float(os.getenv("MIN_EDGE_PP", "0.05"))  # z.B. 0.03..0.07
P_MODEL_BOOST  = float(os.getenv("P_MODEL_BOOST", "0.04"))  # +pp auf q fürs Modell
P_PRED_BOOST   = float(os.getenv("P_PRED_BOOST",  "0.01"))  # +pp auf q fürs Prediction-Signal
DEBUG          = os.getenv("DEBUG", "false").lower() in ("1","true","yes","on")

# --- Fallback: Teams/League/Kickoff nachladen, falls odds?date sie nicht liefert
def fetch_fixture_meta(fixture_id: int):
    try:
        r = requests.get(f"{API}/fixtures", params={"id": fixture_id}, headers=HDR, timeout=30)
        r.raise_for_status()
        data = r.json().get("response", [])
        if not data:
            return None, None, None, None
        row = data[0]
        league_id    = (row.get("league") or {}).get("id")
        home_team_id = ((row.get("teams") or {}).get("home") or {}).get("id")
        away_team_id = ((row.get("teams") or {}).get("away") or {}).get("id")
        kickoff_iso  = ((row.get("fixture") or {}).get("date") or "").replace("Z","+00:00")
        kickoff_utc  = None
        try:
            kickoff_utc = dt.datetime.fromisoformat(kickoff_iso)
        except Exception:
            pass
        return league_id, home_team_id, away_team_id, kickoff_utc
    except Exception as e:
        if DEBUG:
            print(f"[WARN] fetch_fixture_meta({fixture_id}) failed: {e}")
        return None, None, None, None

def today_str():    return dt.datetime.now(TZ).date().isoformat()
def tomorrow_str(): return (dt.datetime.now(TZ).date() + dt.timedelta(days=1)).isoformat()

def implied_prob_from_price(price: float) -> float:
    try:
        p = float(price)
        return 0.0 if p <= 0 else 1.0/p
    except Exception:
        return 0.0

def ensemble_p(q_market: float, p_model: float, p_pred: float) -> float:
    # Startgewichte – später kalibrieren
    return 0.35*q_market + 0.40*p_model + 0.25*p_pred

def upsert_candidate(cur, cand):
    cur.execute("""
      INSERT INTO gb_prematch_candidates
        (fixture_id, day_bucket, kickoff_utc, league_id, home_team_id, away_team_id,
         market, selection, line, best_price, q_implied, p_est, edge_pp, confidence,
         source_flags, status, created_at, updated_at)
      VALUES
        (%(fixture_id)s, %(day_bucket)s, %(kickoff_utc)s, %(league_id)s,
         %(home_team_id)s, %(away_team_id)s, %(market)s, %(selection)s,
         %(line)s, %(best_price)s, %(q_implied)s, %(p_est)s, %(edge_pp)s,
         %(confidence)s, %(source_flags)s, %(status)s, now(), now())
      ON CONFLICT (fixture_id, market, selection)
      DO UPDATE SET
         day_bucket   = EXCLUDED.day_bucket,
         kickoff_utc  = EXCLUDED.kickoff_utc,
         best_price   = EXCLUDED.best_price,
         q_implied    = EXCLUDED.q_implied,
         p_est        = EXCLUDED.p_est,
         edge_pp      = EXCLUDED.edge_pp,
         confidence   = EXCLUDED.confidence,
         source_flags = EXCLUDED.source_flags,
         status       = 'ACTIVE',
         updated_at   = now();
    """, cand)

def freeze_to_event(cur, cand):
    cur.execute("""
      INSERT INTO gb_tip_events
        (fixture_id, kickoff_utc, league_id, home_team_id, away_team_id, market,
         selection, line, best_price, q_implied, p_est, edge_pp, confidence,
         reason_code, rationale, source_candidate, published_at, status)
      VALUES
        (%(fixture_id)s, %(kickoff_utc)s, %(league_id)s, %(home_team_id)s, %(away_team_id)s,
         %(market)s, %(selection)s, %(line)s, %(best_price)s, %(q_implied)s,
         %(p_est)s, %(edge_pp)s, %(confidence)s, %(reason_code)s, %(rationale)s,
         %(source_candidate)s, now(), 'OPEN')
      ON CONFLICT (fixture_id, market, selection) DO NOTHING;
    """, cand)

def fetch_odds_by_date(date_iso: str):
    r = requests.get(f"{API}/odds", params={"date": date_iso}, headers=HDR, timeout=60)
    r.raise_for_status()
    return r.json().get("response", [])

def main():
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cur  = conn.cursor()

    for bucket, d in (("TODAY", today_str()), ("TOMORROW", tomorrow_str())):
        odds = fetch_odds_by_date(d)

        total = len(odds)
        with_books = 0
        with_best = 0
        written = 0
        skipped_edge = 0
        skipped_missing = 0

        if DEBUG:
            print(f"[{bucket}] odds records: {total}")

        for item in odds:
            f    = item.get("fixture") or {}
            league = item.get("league") or {}
            teams  = item.get("teams")  or {}
            books  = item.get("bookmakers") or []
            if not books:
                continue
            with_books += 1

            fixture_id = f.get("id")
            if not fixture_id:
                continue

            # Kickoff
            kickoff_utc = None
            try:
                kickoff_utc = dt.datetime.fromisoformat((f.get("date") or "").replace("Z","+00:00"))
            except Exception:
                pass

            league_id    = league.get("id")
            home_team_id = ((teams.get("home") or {}).get("id") if teams else None)
            away_team_id = ((teams.get("away") or {}).get("id") if teams else None)

            # Fallback via /fixtures?id
            if not (league_id and home_team_id and away_team_id and kickoff_utc):
                LID, HT, AT, KO = fetch_fixture_meta(fixture_id)
                league_id    = league_id or LID
                home_team_id = home_team_id or HT
                away_team_id = away_team_id or AT
                kickoff_utc  = kickoff_utc or KO

            if not (home_team_id and away_team_id and kickoff_utc):
                skipped_missing += 1
                if DEBUG:
                    print(f"[skip missing] fixture {fixture_id}: league/team/ko missing")
                continue

            # FT 1X2 – beste Home-Quote bestimmen
            best_home = None
            for b in books:
                for m in b.get("bets", []):
                    if m.get("name") in ("Match Winner","Fulltime Result"):
                        for v in m.get("values", []):
                            if v.get("value") in ("Home","1"):
                                try:
                                    price = float(v["odd"])
                                    best_home = max(best_home, price) if best_home else price
                                except Exception:
                                    pass

            if not best_home:
                continue
            with_best += 1

            # q, p, edge berechnen (mit Boosts)
            q = implied_prob_from_price(best_home)
            p_model = min(0.95, max(0.01, q + P_MODEL_BOOST))
            p_pred  = min(0.95, max(0.01, q + P_PRED_BOOST))
            p = ensemble_p(q, p_model, p_pred)
            edge = p - q

            if DEBUG:
                print(f"[calc] fx={fixture_id} q={q:.3f} p_model={p_model:.3f} p_pred={p_pred:.3f} p={p:.3f} edge={edge:.3f}")

            if edge < MIN_EDGE_PP:
                skipped_edge += 1
                if DEBUG:
                    print(f"[skip edge] fx={fixture_id} edge={edge:.3f} < {MIN_EDGE_PP:.3f}")
                continue

            cand = dict(
                fixture_id=fixture_id,
                day_bucket=bucket,
                kickoff_utc=kickoff_utc,
                league_id=league_id,
                home_team_id=home_team_id,
                away_team_id=away_team_id,
                market="FT_1X2",
                selection="HOME",
                line=None,
                best_price=best_home,
                q_implied=round(q,4),
                p_est=round(p,4),
                edge_pp=round(edge,4),
                confidence=0.70,
                source_flags=['MARKET','MODEL'] + (['PREDICTIONS'] if P_PRED_BOOST > 0 else []),
                status='ACTIVE'
            )
            upsert_candidate(cur, cand)
            written += 1

            # Freeze: wenn Anpfiff in <= 60 Minuten
            try:
                mins_to_kick = (kickoff_utc - dt.datetime.now(dt.timezone.utc)).total_seconds()/60
                if mins_to_kick <= 60:
                    cand_ev = cand.copy()
                    cand_ev.update(dict(
                        reason_code='VALUE_PREMATCH',
                        rationale='Value vs. Markt',
                        source_candidate=None
                    ))
                    freeze_to_event(cur, cand_ev)
            except Exception:
                pass

        conn.commit()
        if DEBUG:
            print(f"[{bucket}] with_books={with_books} with_best={with_best} written={written} "
                  f"skipped_missing={skipped_missing} skipped_edge={skipped_edge}")

    cur.close(); conn.close()

if __name__ == "__main__":
    main()
