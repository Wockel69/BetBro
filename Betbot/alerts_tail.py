#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, json, sys, datetime as dt
from dotenv import load_dotenv
from sqlalchemy import select, desc
from sqlalchemy.orm import Session
from db_models import SessionLocal, Fixture, Alert

load_dotenv()

def pretty_ts(t):
    if isinstance(t, dt.datetime):
        return t.strftime("%Y-%m-%d %H:%M:%S")
    return str(t)

def print_alerts(limit: int = 20):
    with SessionLocal() as sess:
        q = (
            select(Alert, Fixture.home_name, Fixture.away_name)
            .join(Fixture, Fixture.fixture_id == Alert.fixture_id)
            .order_by(desc(Alert.id))
            .limit(limit)
        )
        rows = sess.execute(q).all()

    if not rows:
        print("Keine Alerts.")
        return

    for (a, home, away) in rows:
        teams = f"{home} – {away}" if home and away else ""
        print(f"[{pretty_ts(a.ts_utc)}] {a.kind} {teams}")
        print(f"  {a.message}")
        if a.details:
            try:
                d = json.loads(a.details)
                print(f"  Details: {d}")
            except Exception:
                print(f"  Details: {a.details}")
        print("-" * 60)

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    print_alerts(n)
