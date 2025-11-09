import os
from typing import Dict, Any, List
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from dotenv import load_dotenv

load_dotenv()
ENGINE: Engine = create_engine(os.getenv("DATABASE_URL"), pool_pre_ping=True, future=True)

def upsert_fixture(fix: Dict[str, Any]) -> None:
    sql = text("""
        INSERT INTO fixtures (fixture_id, league_id, league_name, season,
                              home_id, home_name, away_id, away_name, created_at, updated_at)
        VALUES (:fixture_id, :league_id, :league_name, :season,
                :home_id, :home_name, :away_id, :away_name, now(), now())
        ON CONFLICT (fixture_id) DO UPDATE
           SET league_id=:league_id, league_name=:league_name, season=:season,
               home_id=:home_id, home_name=:home_name, away_id=:away_id, away_name=:away_name,
               updated_at=now();
    """)
    with ENGINE.begin() as conn:
        conn.execute(sql, fix)

def insert_snapshot(rec: Dict[str, Any]) -> None:
    keys = ", ".join(rec.keys())
    vals = ", ".join([f":{k}" for k in rec.keys()])
    sql = text(f"INSERT INTO snapshots ({keys}) VALUES ({vals});")
    with ENGINE.begin() as conn:
        conn.execute(sql, rec)

def insert_odds_bulk(rows: List[Dict[str, Any]]) -> None:
    if not rows: return
    keys = rows[0].keys()
    keys_sql = ", ".join(keys)
    vals_sql = ", ".join([f":{k}" for k in keys])
    sql = text(f"INSERT INTO odds_live ({keys_sql}) VALUES ({vals_sql});")
    with ENGINE.begin() as conn:
        conn.execute(sql, rows)

def insert_alert(alert: Dict[str, Any]) -> None:
    sql = text("""
        INSERT INTO alerts (fixture_id, kind, message, details)
        VALUES (:fixture_id, :kind, :message, :details);
    """)
    with ENGINE.begin() as conn:
        conn.execute(sql, alert)
