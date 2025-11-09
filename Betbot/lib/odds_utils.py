# -*- coding: utf-8 -*-
"""
Utility-Funktionen für das Aggregieren und De-Viggen von Wettquoten.
Verwendet von GambleBros-Workern (z.B. prematch_15min.py, prematch_overmorrow.py)
Autor: Tobias / GambleBros Projekt
"""

import statistics

# ============================================================
# 📊 Allgemeine Hilfsfunktionen
# ============================================================

def devig_1x2(odds_home, odds_draw=None, odds_away=None):
    """
    Entfernt die Buchmacher-Marge (Vig) für 1X2- oder 2-Wege-Märkte.
    Für 2-Wege-Märkte kann odds_draw=None bleiben.
    Gibt normalisierte Wahrscheinlichkeiten zurück.
    """
    try:
        # Für 2-Wege-Markt (z.B. Over/Under, BTTS)
        if odds_draw is None or odds_away is None:
            probs = [1/float(odds_home), 1/float(odds_draw or odds_away)]
        else:
            probs = [1/float(odds_home), 1/float(odds_draw), 1/float(odds_away)]
        s = sum(probs)
        return [p/s for p in probs]
    except Exception:
        return [0, 0, 0]


# ============================================================
# 🏆 Match Winner (1X2)
# ============================================================

def aggregate_market_odds(bookmakers, market_name="Match Winner"):
    """
    Aggregiert Quoten über alle Bookies für den 1X2-Markt.
    Gibt Durchschnittsquoten und Markt-Wahrscheinlichkeiten zurück.
    """
    odds_home, odds_draw, odds_away = [], [], []

    for b in bookmakers:
        for bet in b.get("bets", []):
            if bet.get("name") == market_name:
                for v in bet.get("values", []):
                    try:
                        val, odd = v["value"], float(v["odd"])
                        if val in ("Home", "1"):
                            odds_home.append(odd)
                        elif val in ("Draw", "X"):
                            odds_draw.append(odd)
                        elif val in ("Away", "2"):
                            odds_away.append(odd)
                    except:
                        continue

    if not odds_home or not odds_draw or not odds_away:
        return None

    mean_home = statistics.mean(odds_home)
    mean_draw = statistics.mean(odds_draw)
    mean_away = statistics.mean(odds_away)

    p_home, p_draw, p_away = devig_1x2(mean_home, mean_draw, mean_away)

    return dict(
        home=mean_home,
        draw=mean_draw,
        away=mean_away,
        p_home=p_home,
        p_draw=p_draw,
        p_away=p_away,
        n=len(odds_home)
    )


# ============================================================
# ⚽ Over/Under (z. B. 2.5 Tore)
# ============================================================

def aggregate_over_under(bookmakers, line="2.5", market_name="Goals Over/Under"):
    """
    Aggregiert Over/Under-Quoten über alle Bookies für eine bestimmte Torlinie.
    Beispiel: aggregate_over_under(data, line="2.5")
    """
    odds_over, odds_under = [], []

    for b in bookmakers:
        for bet in b.get("bets", []):
            if bet.get("name") == market_name:
                for v in bet.get("values", []):
                    try:
                        val, odd = v["value"], float(v["odd"])
                        if val.strip() == f"Over {line}":
                            odds_over.append(odd)
                        elif val.strip() == f"Under {line}":
                            odds_under.append(odd)
                    except:
                        continue

    if not odds_over or not odds_under:
        return None

    mean_over = statistics.mean(odds_over)
    mean_under = statistics.mean(odds_under)
    # 2-Wege-Markt, Draw-Dummy für devig_1x2
    p_over, p_under = devig_1x2(mean_over, mean_under)[:2]

    return dict(
        over=mean_over,
        under=mean_under,
        p_over=p_over,
        p_under=p_under,
        n=len(odds_over)
    )


# ============================================================
# 🤝 Both Teams To Score (BTTS)
# ============================================================

def aggregate_btts(bookmakers, market_name="Both Teams Score"):
    """
    Aggregiert Quoten für Both Teams To Score (Yes/No).
    Beispiel: aggregate_btts(data)
    """
    odds_yes, odds_no = [], []

    for b in bookmakers:
        for bet in b.get("bets", []):
            if bet.get("name") == market_name:
                for v in bet.get("values", []):
                    try:
                        val, odd = v["value"].strip().lower(), float(v["odd"])
                        if val in ("yes", "y"):
                            odds_yes.append(odd)
                        elif val in ("no", "n"):
                            odds_no.append(odd)
                    except:
                        continue

    if not odds_yes or not odds_no:
        return None

    mean_yes = statistics.mean(odds_yes)
    mean_no = statistics.mean(odds_no)
    p_yes, p_no = devig_1x2(mean_yes, mean_no)[:2]

    return dict(
        yes=mean_yes,
        no=mean_no,
        p_yes=p_yes,
        p_no=p_no,
        n=len(odds_yes)
    )


# ============================================================
# 🧪 Quick Test (nur manuell ausführen)
# ============================================================

if __name__ == "__main__":
    import os, requests, json
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=".env_gamblebros")

    API = os.getenv("APIFOOTBALL_BASE")
    KEY = os.getenv("APIFOOTBALL_KEY")
    HDR = {"x-apisports-key": KEY}

    r = requests.get(f"{API}/odds", params={"date": "2025-10-26"}, headers=HDR, timeout=60)
    data = r.json()["response"][0]["bookmakers"]

    print("✅ 1X2:", json.dumps(aggregate_market_odds(data), indent=2))
    print("✅ OU 2.5:", json.dumps(aggregate_over_under(data, "2.5"), indent=2))
    print("✅ BTTS:", json.dumps(aggregate_btts(data), indent=2))
