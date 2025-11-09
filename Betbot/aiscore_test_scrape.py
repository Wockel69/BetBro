#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio, csv, os, re, unicodedata, time
from datetime import datetime, timezone
from typing import Optional, Tuple
from difflib import SequenceMatcher
from playwright.async_api import async_playwright, TimeoutError as PWTimeout, Page

# ================== Konfiguration ==================
TEAM_HOME = os.getenv("HOME_TEAM", "Deportivo Mixco")
TEAM_AWAY = os.getenv("AWAY_TEAM", "CD Argentino Monte Maiz")
SCRAPE_INTERVAL_SEC = int(os.getenv("SCRAPE_INTERVAL_SEC", "30"))
OUT_CSV = os.getenv("OUT_CSV", "aiscore_stats_log.csv")
HEADLESS = os.getenv("HEADLESS", "true").lower() in ("1","true","yes")
TIMEZONE = os.getenv("TZ", "Europe/Berlin")
LOCALE = os.getenv("LOCALE", "de-DE")

FUZZY_THRESHOLD = float(os.getenv("FUZZY_THRESHOLD", "0.72"))
SINGLE_TEAM_MATCH = os.getenv("SINGLE_TEAM_MATCH", "1") in ("1","true","yes")
SINGLE_TEAM_THRESHOLD = float(os.getenv("SINGLE_TEAM_THRESHOLD", "0.85"))
MATCH_DEBUG = os.getenv("MATCH_DEBUG", "0") in ("1","true","yes")

SCAN_POLL_MS = int(os.getenv("SCAN_POLL_MS", "300"))
SCAN_MAX_SEC  = int(os.getenv("SCAN_MAX_SEC", "120"))
SCROLL_STEP_PX = int(os.getenv("SCROLL_STEP_PX", "280"))
SCROLL_PAUSE_MS = int(os.getenv("SCROLL_PAUSE_MS", "420"))
SCROLL_IDLE_ROUNDS = int(os.getenv("SCROLL_IDLE_ROUNDS", "6"))

def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ================== Teamname-Normalisierung & Fuzzy ==================
_STOPWORDS = {
    "fc","cf","sc","afc","ac","as","bc","bk","fk","sk","kf","ks","od","nk","cd","sd","ss","ssc",
    "u","u17","u18","u19","u20","u21","u22","u23","b","ii","iii","2","team","club","deportivo",
    "atletico","athletic","real","sporting","univ","universidad","borussia","sv","tsv","ssv",
    "c.f.","s.c.","f.c.","s.s.","c.d.","c.a.","c.s.","cf.","sc.","fc.","sad","sp.","calcio",
    "city","united","utd"
}
_ALIAS = {
    "cote divoire": "ivory coast", "cote d ivoire": "ivory coast", "cote-d-ivoire": "ivory coast",
    "côte d ivoire": "ivory coast", "korea republic": "south korea", "republic of korea": "south korea",
    "korea dpr": "north korea", "viet nam": "vietnam", "u s a": "usa", "u.s.a": "usa",
    "uae": "united arab emirates", "u.a.e": "united arab emirates", "dr congo": "congo dr",
    "congo drc": "congo dr", "cape verde": "cabo verde", "czechia": "czech republic",
    "fyrom": "north macedonia", "bosnia herzegovina": "bosnia and herzegovina",
    "man utd": "manchester united", "man united": "manchester united", "man u": "manchester united",
    "man city": "manchester city", "psg": "paris saint germain", "inter": "inter milan",
    "bayern": "bayern munich", "ath bilbao": "athletic bilbao",
}

def _strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))

def _clean(s: str) -> str:
    s = _strip_accents(s.lower())
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def canonical_team(s: str) -> str:
    s = _clean(s)
    s = _ALIAS.get(s, s)
    tokens = [t for t in s.split() if t not in _STOPWORDS]
    if tokens and tokens[-1] in {"u19","u20","u21","u23","b","ii","iii","2"}:
        tokens = tokens[:-1]
    return " ".join(tokens).strip()

def fuzzy_score(a: str, b: str) -> float:
    ca, cb = canonical_team(a), canonical_team(b)
    r1 = SequenceMatcher(None, ca, cb).ratio()
    ta, tb = set(ca.split()), set(cb.split())
    r2 = (len(ta & tb) / len(ta | tb)) if (ta or tb) else 0.0
    return 0.6*r1 + 0.4*r2

def pair_scores(q_home: str, q_away: str, cand_home: str, cand_away: str):
    n_hh = fuzzy_score(q_home, cand_home)
    n_aa = fuzzy_score(q_away, cand_away)
    s_ha = fuzzy_score(q_home, cand_away)
    s_ah = fuzzy_score(q_away, cand_home)
    normal_pair  = min(n_hh, n_aa)
    swapped_pair = min(s_ha, s_ah)
    single_best  = max(n_hh, n_aa, s_ha, s_ah)
    return normal_pair, swapped_pair, single_best

# ================== Client-Side Stats Reader ==================
MUTATION_OBSERVER_JS = r"""
(() => {
  const onlyNum = s => {
    if (s==null) return null;
    const t = String(s).replace(",", ".").replace(/[^0-9.\-]/g,"").trim();
    if (!t) return null;
    const v = parseFloat(t);
    return Number.isFinite(v) ? v : null;
  };
  const q = (root, sel) => { try { return root.querySelector(sel); } catch(_) { return null; } };
  const qa = (root, sel) => { try { return Array.from(root.querySelectorAll(sel)); } catch(_) { return []; } };

  function getMinute(){
    const cand = [".match-status",".ai-match-status",".status",".time",".score-time",".status-time",".m-time",".timer",".matchTime"];
    for(const s of cand){
      const el = document.querySelector(s);
      if(!el) continue;
      const t = el.textContent.trim();
      const mmss = t.match(/(\d{1,2})\s*:\s*\d{2}/);
      if(mmss) return parseInt(mmss[1],10);
      const m = t.match(/(\d{1,3})\s*[’']/);
      if(m) return parseInt(m[1],10);
      if(/finished|ft|ended|full\s*time/i.test(t)) return 100;
    }
    return null;
  }

  function readPossession(){
    const out = {possession_h:null, possession_a:null};
    const home = document.querySelector(".stats .home");
    const away = document.querySelector(".stats .away");
    const getPerc = (root, side) => {
      if(!root) return null;
      const pb = root.querySelector("[role='progressbar'][aria-valuenow]");
      if (pb) {
        const v = parseFloat(pb.getAttribute("aria-valuenow"));
        if (Number.isFinite(v)) return v;
      }
      const txt = side==="h"
        ? (q(root, ".ml-20.mr-xs") || q(root, ".mr-xs"))
        : (q(root, ".mr-20.ml-xs") || q(root, ".ml-xs"));
      return onlyNum(txt ? txt.textContent : null);
    };
    out.possession_h = getPerc(home, "h");
    out.possession_a = getPerc(away, "a");
    return out;
  }

  function readCardsCorners(){
    const out = {corners_h:null,corners_a:null,yellow_h:null,yellow_a:null,red_h:null,red_a:null};
    const home = document.querySelector(".stats .home");
    const away = document.querySelector(".stats .away");
    const get = (root, sel) => root ? onlyNum((q(root, sel) || {}).textContent || null) : null;
    out.corners_h = get(home, ".corners");
    out.red_h     = get(home, ".red-card");
    out.yellow_h  = get(home, ".yellow-card");
    out.corners_a = get(away, ".corners");
    out.red_a     = get(away, ".red-card");
    out.yellow_a  = get(away, ".yellow-card");
    return out;
  }

  function mapLabel(txt){
    const t = (txt||"").trim().toLowerCase();
    if (!t) return null;
    if (t.includes("dangerous") && t.includes("attacks")) return "dangerous_attacks";
    if (t === "attacks" || (t.includes("attacks") && !t.includes("danger"))) return "attacks";
    if (t === "shots") return "shots";
    if (t.includes("shots on")) return "shots_on";
    if (t.includes("shots off")) return "shots_off";
    return null;
  }

  function readGrid(){
    const out = {};
    const grid = document.querySelector(".stats2.w-bar-100");
    if(!grid) return out;

    const centers = qa(grid, ".text-center, .name, .label, .title, .stat-name");
    centers.forEach(center => {
      const key = mapLabel(center.textContent);
      if (!key) return;
      const row = center.closest(".flex.border-box");
      if (!row) return;

      const boxes = qa(row, ":scope > .flex.flex-1");
      if (boxes.length < 2) return;

      const left  = boxes[0];
      const right = boxes[1];

      const leftNum  = onlyNum((q(left , ".mr-xs, .mr-xxs, div")||{}).textContent || left.textContent);
      const rightNum = onlyNum((q(right, ".ml-xs, .ml-xxs, div:last-child, div")||{}).textContent || right.textContent);

      if (key==="attacks"){ out.attacks_h = leftNum; out.attacks_a = rightNum; }
      if (key==="dangerous_attacks"){ out.dangerous_h = leftNum; out.dangerous_a = rightNum; }
      if (key==="shots"){ out.shots_h = leftNum; out.shots_a = rightNum; }
      if (key==="shots_on"){ out.sog_h = leftNum; out.sog_a = rightNum; }
      if (key==="shots_off"){ out.soff_h = leftNum; out.soff_a = rightNum; }
    });

    return out;
  }

  function readAll(){
    const base = {
      minute: getMinute(),
      possession_h:null, possession_a:null,
      attacks_h:null, attacks_a:null,
      dangerous_h:null, dangerous_a:null,
      shots_h:null, shots_a:null,
      sog_h:null, sog_a:null,
      soff_h:null, soff_a:null,
      corners_h:null, corners_a:null,
      yellow_h:null, yellow_a:null,
      red_h:null, red_a:null
    };
    Object.assign(base, readGrid());
    Object.assign(base, readPossession());
    Object.assign(base, readCardsCorners());
    return base;
  }

  window.__bbStats = readAll();
  const mo = new MutationObserver(()=>{ try{ window.__bbStats = readAll(); }catch(e){} });
  mo.observe(document.body, {subtree:true, childList:true, characterData:true});
  setInterval(()=>{ try{ window.__bbStats = readAll(); }catch(e){} }, 2000);
  return true;
})();
"""

# ================== Helpers ==================
async def dismiss_banners(page: Page):
    for sel in [
        "#onetrust-accept-btn-handler","button:has-text('Accept all')","button:has-text('Akzeptieren')",
        ".ot-sdk-button",".cookie-accept",".btn-accept",".fc-cta-consent","button[mode='primary']",
    ]:
        try:
            el = await page.wait_for_selector(sel, timeout=2000)
            if el: await el.click(); await asyncio.sleep(0.2); break
        except Exception:
            pass

async def extract_card_teams(a_el) -> Tuple[str, str]:
    try:
        ht = await a_el.locator("span[itemprop='homeTeam']").inner_text(timeout=400)
        at = await a_el.locator("span[itemprop='awayTeam']").inner_text(timeout=400)
        if ht and at: return ht.strip(), at.strip()
    except Exception:
        pass
    for hs, asel in [
        (".teamHomeBox .nameBox", ".teamAwayBox .nameBox"),
        (".home .nameBox", ".away .nameBox"),
        (".home .name", ".away .name"),
    ]:
        try:
            ht = await a_el.locator(hs).inner_text(timeout=300)
            at = await a_el.locator(asel).inner_text(timeout=300)
            if ht and at: return ht.strip(), at.strip()
        except Exception:
            pass
    try:
        raw = await a_el.inner_text(timeout=250)
        raw = re.sub(r"\s+", " ", raw.strip())
        parts = re.split(r"\s+vs\s+|\s+-\s+|\s+—\s+|\s+v\s+", raw, flags=re.I)
        if len(parts) >= 2: return parts[0].strip(), parts[1].strip()
    except Exception:
        pass
    return "", ""

# ================== Aktives Scannen mit Smooth-Scroll ==================
async def active_scan_with_scroll(page: Page, home: str, away: str) -> Optional[str]:
    start = time.time()
    idle_rounds = 0
    last_count = -1
    tick = 0

    async def scan_once() -> Optional[str]:
        nonlocal idle_rounds, last_count
        cards = page.locator("a.match-container, a[href*='/match-']")
        n = await cards.count()
        if n == last_count: idle_rounds += 1
        else: idle_rounds = 0
        last_count = n

        best = {"href": None, "pair": -1.0, "single": -1.0}

        for i in range(n):
            a_el = cards.nth(i)
            ht, at = await extract_card_teams(a_el)
            if not ht or not at:
                continue
            n_pair, s_pair, single_best = pair_scores(home, away, ht, at)
            pair_best = max(n_pair, s_pair)
            if MATCH_DEBUG:
                print(f"[DBG] card {i}: '{ht}' vs '{at}' | pair={pair_best:.3f} single={single_best:.3f}")

            if pair_best >= FUZZY_THRESHOLD:
                href = await a_el.get_attribute("href")
                if href and href.startswith("/"): href = "https://www.aiscore.com"+href
                print(f"[{ts()}] Treffer: {canonical_team(ht)} vs {canonical_team(at)} -> {href}")
                return href

            if SINGLE_TEAM_MATCH and single_best >= SINGLE_TEAM_THRESHOLD:
                href = await a_el.get_attribute("href")
                if href and href.startswith("/"): href = "https://www.aiscore.com"+href
                if single_best > best["single"] or (abs(single_best - best["single"]) < 1e-6 and pair_best > best["pair"]):
                    best = {"href": href, "pair": pair_best, "single": single_best}

        if best["href"]:
            print(f"[{ts()}] 1-Team-Treffer (score={best['single']:.3f}, tiebreak={best['pair']:.3f}) -> {best['href']}")
            return best["href"]
        return None

    while time.time() - start < SCAN_MAX_SEC:
        href = await scan_once()
        if href:
            return href

        if tick % max(1, SCROLL_PAUSE_MS // SCAN_POLL_MS) == 0:
            for sel in [".live-list", ".list", ".match-list", "main", "body"]:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        await el.evaluate(f"e => e.scrollBy(0, {SCROLL_STEP_PX})")
                except Exception:
                    pass
            try:
                await page.evaluate(f"window.scrollBy(0, {SCROLL_STEP_PX})")
            except Exception:
                pass
            await asyncio.sleep(SCROLL_PAUSE_MS / 1000.0)

        if idle_rounds >= SCROLL_IDLE_ROUNDS:
            break

        await asyncio.sleep(SCAN_POLL_MS / 1000.0)
        tick += 1

    return None

async def find_and_open_match(page: Page, home: str, away: str) -> bool:
    try:
        await page.wait_for_selector("a.match-container, a[href*='/match-']", timeout=8000)
    except Exception:
        pass

    href = await active_scan_with_scroll(page, home, away)
    if not href:
        try: await page.evaluate("window.scrollTo(0,0)")
        except Exception: pass
        href = await active_scan_with_scroll(page, home, away)

    if href:
        await page.goto(href, wait_until="domcontentloaded")
        return True
    return False

# ================== Main ==================
async def main():
    print(f"[{ts()}] Start – Ziel: {TEAM_HOME} vs {TEAM_AWAY} – Intervall {SCRAPE_INTERVAL_SEC}s")

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=HEADLESS,
        args=["--disable-blink-features=AutomationControlled","--no-sandbox","--disable-dev-shm-usage"],
    )
    context = await browser.new_context(
        locale=LOCALE, timezone_id=TIMEZONE,
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"),
        viewport={"width":1600,"height":1000},
    )
    # Stealth & Link-Interceptor: target=_blank -> _self, window.open -> same-tab
    await context.add_init_script("""
      Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
      window.open = (url) => { if (url) location.href = url; return null; };
      document.addEventListener('click', (e) => {
        const a = e.target && e.target.closest && e.target.closest('a[target="_blank"]');
        if (a) a.setAttribute('target','_self');
      }, true);
    """)

    page = await context.new_page()

    # *** SAUBERE POPUP-HANDLER (kein Lambda, kein globals.update) ***
    def handle_new_page(p: Page):
        nonlocal page
        page = p
    context.on("page", handle_new_page)
    page.on("popup", handle_new_page)

    # Startseite / Live
    try:
        await page.goto("https://www.aiscore.com/", wait_until="domcontentloaded", timeout=30000)
    except PWTimeout:
        pass
    await dismiss_banners(page)

    try:
        btn = await page.wait_for_selector("a:has-text('Live'), button:has-text('Live')", timeout=8000)
        if btn:
            # Falls ein Popup entsteht, direkt übernehmen
            popup_task = context.expect_page()
            await btn.click()
            try:
                newp = await popup_task.value
                if newp:
                    page = newp
            except Exception:
                pass
            await page.wait_for_load_state("domcontentloaded", timeout=20000)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
    except Exception:
        try:
            await page.goto("https://www.aiscore.com/?c=live", wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
    await dismiss_banners(page)

    # Match finden
    ok = await find_and_open_match(page, TEAM_HOME, TEAM_AWAY)
    if not ok:
        print(f"[{ts()}] ❌ Match nicht gefunden – Screenshot.")
        try:
            await page.screenshot(path="not_found.png", full_page=True)
            print("[INFO] Screenshot: not_found.png")
        except Exception:
            pass
        await context.close(); await browser.close(); await pw.stop()
        return

    # Stats
    try:
        await page.wait_for_selector(".stats2.w-bar-100, .ai-statistics, .statistics, .match-statistics, .stats", timeout=20000)
    except Exception:
        pass
    await page.evaluate(MUTATION_OBSERVER_JS)

    # CSV
    header = ["ts_utc","minute","possession_h","possession_a","attacks_h","attacks_a","dangerous_h","dangerous_a","shots_h","shots_a","sog_h","sog_a","soff_h","soff_a","corners_h","corners_a","yellow_h","yellow_a","red_h","red_a"]
    new_file = not os.path.exists(OUT_CSV)
    with open(OUT_CSV,"a",newline="",encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file: w.writerow(header)
        try:
            while True:
                snap = await page.evaluate("window.__bbStats || null")
                if not snap:
                    await asyncio.sleep(1.0); continue
                row = [ts(), snap.get("minute"), snap.get("possession_h"), snap.get("possession_a"), snap.get("attacks_h"), snap.get("attacks_a"), snap.get("dangerous_h"), snap.get("dangerous_a"), snap.get("shots_h"), snap.get("shots_a"), snap.get("sog_h"), snap.get("sog_a"), snap.get("soff_h"), snap.get("soff_a"), snap.get("corners_h"), snap.get("corners_a"), snap.get("yellow_h"), snap.get("yellow_a"), snap.get("red_h"), snap.get("red_a")]
                w.writerow(row); f.flush()
                print(f"[{ts()}] OK min={snap.get('minute')}  Pos {snap.get('possession_h')}%-{snap.get('possession_a')}%  Att {snap.get('attacks_h')}-{snap.get('attacks_a')}  DAtt {snap.get('dangerous_h')}-{snap.get('dangerous_a')}  Shots {snap.get('shots_h')}-{snap.get('shots_a')}  SOG {snap.get('sog_h')}-{snap.get('sog_a')}  SOFF {snap.get('soff_h')}-{snap.get('soff_a')}  Corn {snap.get('corners_h')}-{snap.get('corners_a')}  Cards {snap.get('yellow_h')}/{snap.get('red_h')} - {snap.get('yellow_a')}/{snap.get('red_a')}")
                await asyncio.sleep(SCRAPE_INTERVAL_SEC)
        except KeyboardInterrupt:
            print(f"\n[{ts()}] Ende (User).")
        finally:
            await context.close(); await browser.close(); await pw.stop()

if __name__ == "__main__":
    asyncio.run(main())
