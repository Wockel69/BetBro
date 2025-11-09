#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
from typing import List, Any, Dict

from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import psycopg2
import psycopg2.extras

APP_VERSION = "1.0.0"

app = FastAPI(title="GambleBros Read-only API", version=APP_VERSION)

# CORS (nur wenn gesetzt)
CORS_ALLOW = [o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "").split(",") if o.strip()]
if CORS_ALLOW:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ALLOW,
        allow_credentials=False,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["*"],
    )

# Optionales Shared-Secret
API_SHARED_KEY = os.getenv("API_SHARED_KEY", "").strip() or None

# DB: bevorzugt Read-Only, sonst normal
DB_URL = os.getenv("RO_DATABASE_URL") or os.getenv("DATABASE_URL")
if not DB_URL:
    raise RuntimeError("DATABASE_URL/RO_DATABASE_URL nicht gesetzt - bitte in .env_gamblebros hinterlegen.")

def guard(req: Request) -> None:
    if API_SHARED_KEY is None:
        return
    if req.headers.get("x-gb-key") != API_SHARED_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")

def q(sql: str, *args) -> List[Dict[str, Any]]:
    conn = psycopg2.connect(DB_URL)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, args)
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        conn.close()

def valid_day(value: str) -> str:
    v = (value or "TODAY").upper()
    if v not in ("TODAY", "TOMORROW", "OVERMORROW"):
        raise HTTPException(status_code=400, detail="invalid 'day' (TODAY|TOMORROW|OVERMORROW)")
    return v

@app.get("/")
def root():
    return {
        "name": "GambleBros Read-only API",
        "version": APP_VERSION,
        "routes": [
            {"GET": "/api/health"},
            {"GET": "/api/tips?day=TODAY|TOMORROW|OVERMORROW&limit=100"},
            {"GET": "/api/top-picks?days=3"},
        ],
        "auth": "HTTP-Header 'x-gb-key' setzen, wenn API_SHARED_KEY aktiv ist.",
        "note": "Read-only aus gb_prematch_candidates / gb_tip_events."
    }

@app.get("/api/health")
def health(req: Request):
    guard(req)
    rows = q("SELECT now() AS ts, 1 AS ok")
    out = rows[0] if rows else {"ts": None, "ok": 0}
    out["version"] = APP_VERSION
    return out

@app.get("/api/tips")
def tips(req: Request,
         day: str = Query("TODAY", description="TODAY|TOMORROW|OVERMORROW"),
         limit: int = Query(100, ge=1, le=500)):
    guard(req)
    bucket = valid_day(day)
    try:
        rows = q(
            """
            SELECT
              c.fixture_id, c.day_bucket, c.kickoff_utc,
              c.market, c.selection, c.line, c.best_price,
              c.q_implied, c.p_est, c.edge_pp, c.confidence,
              lm.name  AS league_name, lm.logo_url AS league_logo,
              th.name  AS home_name,  th.logo_url AS home_logo,
              ta.name  AS away_name,  ta.logo_url AS away_logo
            FROM gb_prematch_candidates c
            LEFT JOIN league_meta lm ON lm.league_id = c.league_id
            LEFT JOIN team_meta   th ON th.team_id   = c.home_team_id
            LEFT JOIN team_meta   ta ON ta.team_id   = c.away_team_id
            WHERE c.status = 'ACTIVE' AND c.day_bucket = %s
            ORDER BY c.edge_pp DESC, c.kickoff_utc ASC
            LIMIT %s;
            """,
            bucket, limit
        )
        return rows
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"query failed: {e}")

@app.get("/api/top-picks")
def top_picks(req: Request, days: int = Query(3, ge=1, le=7)):
    guard(req)
    try:
        rows = q(
            """
            SELECT
              c.fixture_id, c.kickoff_utc, c.league_id, c.home_team_id, c.away_team_id,
              c.market, c.selection, c.line, c.best_price, c.p_est, c.q_implied,
              c.edge_pp, c.confidence,
              lm.name AS league_name, lm.logo_url AS league_logo,
              th.name AS home_name, th.logo_url AS home_logo,
              ta.name AS away_name, ta.logo_url AS away_logo
            FROM gb_prematch_candidates c
            LEFT JOIN league_meta lm ON lm.league_id = c.league_id
            LEFT JOIN team_meta   th ON th.team_id   = c.home_team_id
            LEFT JOIN team_meta   ta ON ta.team_id   = c.away_team_id
            WHERE c.kickoff_utc <= now() + (make_interval(hours := 24 * %s))
            ORDER BY c.kickoff_utc ASC, c.edge_pp DESC
            LIMIT 200;
            """,
            days
        )
        return rows
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"query failed: {e}")

@app.exception_handler(Exception)
def on_unhandled(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return JSONResponse(status_code=500, content={"detail": f"internal error: {exc}"})
