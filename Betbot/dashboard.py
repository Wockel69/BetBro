# -*- coding: utf-8 -*-
# BetBot – Live Dashboard (Dark UI, Liga-Gruppierung, Logos, Tabs)
# Soft-Refresh via st_autorefresh (kein kompletter Seiten-Reload)
# Start: streamlit run dashboard.py --server.port 8501 --server.address 0.0.0.0

from __future__ import annotations
import os, time
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from collections import defaultdict

import requests
import streamlit as st
from sqlalchemy import func

# ---- Projektmodelle ----
from db_models import SessionLocal, Fixture, Snapshot, OddsLive

# ---- Konfig ----
PAGE_TITLE       = "BetBot – Live Dashboard"
DEFAULT_REFRESH  = int(os.getenv("DASH_REFRESH_SEC", "30"))  # Standard-Intervall (Sekunden)
API_BASE         = "https://v3.football.api-sports.io"
API_KEY          = os.getenv("API_SPORTS_KEY", "")

# ---- Soft Auto-Refresh (ohne kompletten Reload) ----
#   st_autorefresh rendert nur neu, UI-State (Tabs/Filter/Scroll) bleibt erhalten.
try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOREFRESH = True
except Exception:
    HAS_AUTOREFRESH = False  # Fallback weiter unten

# ---- Helper ----
def now_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def status_is_live(short: Optional[str]) -> bool:
    if not short:
        return True
    return short not in {"FT","AET","PEN","PST","CANC","ABD","AWD","WO"}

def score_from_api_node(node: dict) -> str:
    g = (node.get("goals") or {})
    h, a = g.get("home"), g.get("away")
    if h is not None and a is not None:
        return f"{h}–{a}"
    sc = (node.get("score") or {}).get("halftime") or {}
    if sc.get("home") is not None and sc.get("away") is not None:
        return f"{sc.get('home')}–{sc.get('away')}"
    return "—"

@st.cache_data(ttl=20, show_spinner=False)
def fetch_live_fixtures_api() -> List[dict]:
    """Live-Spiele aus API-Football (für Games-Tab)."""
    if not API_KEY:
        return []
    r = requests.get(
        f"{API_BASE}/fixtures",
        headers={"x-apisports-key": API_KEY, "Accept": "application/json"},
        params={"live": "all"},
        timeout=25,
    )
    r.raise_for_status()
    return r.json().get("response", []) or []

def latest_snapshot_for_fixtures(sess, fixture_ids: List[int]) -> Dict[int, Snapshot]:
    """Letzter Snapshot (max minute) pro Fixture aus der DB."""
    if not fixture_ids:
        return {}
    sub = (
        sess.query(Snapshot.fixture_id, func.max(Snapshot.minute).label("m"))
        .filter(Snapshot.fixture_id.in_(fixture_ids))
        .group_by(Snapshot.fixture_id)
        .subquery()
    )
    rows = (
        sess.query(Snapshot)
        .join(sub, (Snapshot.fixture_id == sub.c.fixture_id) & (Snapshot.minute == sub.c.m))
    ).all()
    return {r.fixture_id: r for r in rows}

def safe_get(obj, name, default=None, cast=None):
    if obj is None:
        return default
    val = getattr(obj, name, default)
    if cast and val is not None:
        try:
            return cast(val)
        except Exception:
            return default
    return val

def snapshot_row(fix: Fixture, snap: Optional[Snapshot]) -> Dict[str, Any]:
    row = {
        "Fixture ID": fix.fixture_id,
        "League": getattr(fix, "league_name", ""),
        "Match": f"{getattr(fix,'home_name','?')} vs {getattr(fix,'away_name','?')}",
        "Min": safe_get(snap, "minute", None, int) if snap else None,
        "Shots (H-A)": "–",
        "SOG (H-A)": "–",
        "Corners (H-A)": "–",
        "Poss (H-A)": "–",
    }
    if snap:
        hs = safe_get(snap, "home_shots", 0, int) or 0
        as_ = safe_get(snap, "away_shots", 0, int) or 0
        hS = safe_get(snap, "home_sog", 0, int) or 0
        aS = safe_get(snap, "away_sog", 0, int) or 0
        hc = safe_get(snap, "home_corners", 0, int) or 0
        ac = safe_get(snap, "away_corners", 0, int) or 0
        hp = safe_get(snap, "home_poss", 0.0, float) or 0.0
        ap = safe_get(snap, "away_poss", 0.0, float) or 0.0
        row.update({
            "Shots (H-A)": f"{hs}-{as_}",
            "SOG (H-A)":   f"{hS}-{aS}",
            "Corners (H-A)": f"{hc}-{ac}",
            "Poss (H-A)":  f"{hp:.0f}-{ap:.0f}",
        })
        extras = [
            ("home_soff","away_soff","ShotsOff (H-A)"),
            ("home_saves","away_saves","Saves (H-A)"),
            ("home_yellow","away_yellow","Yellows (H-A)"),
            ("home_red","away_red","Reds (H-A)"),
            ("home_attacks","away_attacks","Attacks (H-A)"),
            ("home_dangerous","away_dangerous","Danger (H-A)"),
        ]
        for h,a,label in extras:
            if hasattr(Snapshot,h) and hasattr(Snapshot,a):
                hv = safe_get(snap,h,0,int) or 0
                av = safe_get(snap,a,0,int) or 0
                row[label] = f"{hv}-{av}"
    return row

# ---- UI Setup (Dark Look) ----
st.set_page_config(page_title=PAGE_TITLE, layout="wide")
st.markdown("""
<style>
:root { --bg:#0f141a; --panel:#171e26; --panel2:#1d2631; --text:#e8edf3; --muted:#9fb0c0; --accent:#00e5c6; --accent2:#25c2ff; }
html, body, [class^="css"]{ background-color:var(--bg); color:var(--text); }
.block-container{ padding-top: 0.8rem; }
div.stTabs [data-baseweb="tab-list"]{ gap:2px; }
div.stTabs [data-baseweb="tab"]{ background:var(--panel); border-radius:8px 8px 0 0; padding:10px 14px; color:var(--text); border:1px solid #0000; }
div.stTabs [aria-selected="true"]{ background:var(--panel2); border-bottom:2px solid var(--accent); }
.small{ color:var(--muted); font-size:0.85rem; }
.card{ background:var(--panel2); border:1px solid #223042; border-radius:12px; padding:12px; }
.league{ background:var(--panel); border:1px solid #223042; border-radius:10px; padding:10px; margin:10px 0 6px; }
.badge{ display:inline-block; padding:2px 8px; background:#263648; color:var(--text); border-radius:999px; font-size:0.75rem; }
.score{ font-weight:800; font-size:1.6rem; }
.min{ color:var(--accent2); font-weight:600; }
.status{ color:var(--accent); font-weight:700; }
</style>
""", unsafe_allow_html=True)

st.title("BetBot – Live Dashboard")
st.caption("Soft Auto-Refresh aktiv • Oben Live (API-Football), unten letzte Stats (DB).")

# ---- Sidebar: Refresh + Ansicht ----
with st.sidebar:
    st.subheader("Aktualisierung")
    refresh_sec = st.slider("Intervall (Sek.)", 10, 120, DEFAULT_REFRESH, step=5)
    st.write("Letztes Laden:", now_utc_str())
    if HAS_AUTOREFRESH:
        st.write("🔄 Soft-Refresh: EIN")
    else:
        st.warning("Soft-Refresh-Modul nicht installiert – `pip install streamlit-autorefresh` (sonst kein Auto-Update).")
    st.divider()
    st.subheader("Ansicht")
    compact = st.toggle("Kompakte Tabellenansicht", value=False)
    per_row = st.slider("Karten pro Zeile", 2, 4, 3)

# ---- Soft-Refresh ausführen (ohne Full-Reload) ----
if HAS_AUTOREFRESH:
    st_autorefresh(interval=refresh_sec * 1000, key="betbot-live-refresh")

tabs = st.tabs(["🟢 GAMES (Live)", "📊 STATS (DB)"])

# =========================
# Tab 1 – GAMES (Live/API)
# =========================
with tabs[0]:
    try:
        lives = fetch_live_fixtures_api()
    except Exception as e:
        st.error(f"API-Fehler: {e}")
        lives = []

    col1, col2, col3 = st.columns([2,2,2])
    leagues = sorted({ (r.get("league") or {}).get("name","?") for r in lives })
    countries = sorted({ (r.get("league") or {}).get("country","?") for r in lives })
    with col1:
        q_country = st.selectbox("Land", ["alle"]+countries, index=0)
    with col2:
        q_league  = st.selectbox("Liga", ["alle"]+leagues, index=0)
    with col3:
        q_text    = st.text_input("Teamsuche", "")

    grouped = defaultdict(list)
    for r in lives:
        lg = r.get("league") or {}
        fix = r.get("fixture") or {}
        tms = r.get("teams") or {}
        if q_country != "alle" and lg.get("country") != q_country: continue
        if q_league  != "alle" and lg.get("name")    != q_league:  continue
        if q_text:
            txt = q_text.lower()
            if txt not in ( (tms.get("home") or {}).get("name","").lower()
                           + " " +
                           (tms.get("away") or {}).get("name","").lower() ):
                continue
        grouped[(lg.get("country","?"), lg.get("name","?"), lg.get("logo"))].append(r)

    if not lives:
        st.info("Laut API derzeit keine Live-Spiele.")
    elif not grouped:
        st.info("Keine Treffer nach Filter.")
    else:
        for (country, league, logo), rows in sorted(grouped.items(), key=lambda x: (x[0][0], x[0][1])):
            st.markdown(f"""<div class="league"><span class="badge">{country}</span> &nbsp; <b>{league}</b></div>""",
                        unsafe_allow_html=True)
            if compact:
                table = []
                for r in rows:
                    fix = r.get("fixture") or {}
                    tms = r.get("teams") or {}
                    table.append({
                        "Fixture ID": fix.get("id"),
                        "Match": f"{(tms.get('home') or {}).get('name','?')} vs {(tms.get('away') or {}).get('name','?')}",
                        "Min": (fix.get("status") or {}).get("elapsed"),
                        "Status": (fix.get("status") or {}).get("short"),
                        "Score": score_from_api_node(r),
                        "Kickoff (UTC)": fix.get("date"),
                        "Venue": (fix.get("venue") or {}).get("name"),
                    })
                st.dataframe(table, use_container_width=True, hide_index=True)
            else:
                rows_sorted = sorted(rows, key=lambda r: ((r.get("fixture") or {}).get("status") or {}).get("elapsed") or -1, reverse=True)
                for i in range(0, len(rows_sorted), per_row):
                    cols = st.columns(per_row)
                    for j in range(per_row):
                        if i + j >= len(rows_sorted): break
                        r = rows_sorted[i+j]
                        fix = r.get("fixture") or {}
                        lg  = r.get("league") or {}
                        tmh = (r.get("teams") or {}).get("home") or {}
                        tma = (r.get("teams") or {}).get("away") or {}
                        score = score_from_api_node(r)
                        with cols[j]:
                            st.markdown('<div class="card">', unsafe_allow_html=True)
                            top = st.columns([1,1,1])
                            with top[0]:
                                if lg.get("logo"): st.image(lg["logo"], width=28)
                                st.caption(lg.get("name",""))
                            with top[1]:
                                st.markdown(f"<div class='status'>{(fix.get('status') or {}).get('short','')}</div>", unsafe_allow_html=True)
                            with top[2]:
                                st.markdown(f"<div class='min'>Min {(fix.get('status') or {}).get('elapsed') or '—'}</div>", unsafe_allow_html=True)
                            mid = st.columns([3,1,3])
                            with mid[0]:
                                if tmh.get("logo"): st.image(tmh["logo"], width=40)
                                st.write(tmh.get("name","—"))
                            with mid[1]:
                                st.markdown(f"<div class='score' style='text-align:center'>{score}</div>", unsafe_allow_html=True)
                            with mid[2]:
                                if tma.get("logo"): st.image(tma["logo"], width=40)
                                st.write(tma.get("name","—"))
                            st.caption(f"ID {fix.get('id')} • { (fix.get('venue') or {}).get('name') or '' } • {fix.get('date')}")
                            st.markdown("</div>", unsafe_allow_html=True)

# =========================
# Tab 2 – STATS (DB)
# =========================
with tabs[1]:
    with SessionLocal() as sess:
        fixtures = sess.query(Fixture).all()
        latest = latest_snapshot_for_fixtures(sess, [f.fixture_id for f in fixtures])
        if not latest:
            st.info("Noch keine Snapshots in der DB.")
        else:
            leagues = sorted({ f.league_name or "?" for f in fixtures if f.fixture_id in latest })
            c1, c2 = st.columns([2,2])
            with c1:
                q_league2 = st.selectbox("Liga (DB-Stats)", ["alle"]+leagues, index=0)
            with c2:
                q_text2 = st.text_input("Teamsuche (DB-Stats)", "")

            data = []
            for f in fixtures:
                if f.fixture_id not in latest: continue
                if q_league2 != "alle" and (f.league_name or "?") != q_league2: continue
                if q_text2:
                    txt = q_text2.lower()
                    if txt not in ( (f.home_name or "").lower() + " " + (f.away_name or "").lower() ):
                        continue
                data.append( snapshot_row(f, latest[f.fixture_id]) )

            if data:
                st.dataframe(data, use_container_width=True, hide_index=True)
            else:
                st.info("Keine DB-Snapshots nach Filter.")
