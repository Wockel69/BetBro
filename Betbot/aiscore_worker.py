#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio, os, re, time, unicodedata
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from difflib import SequenceMatcher
from playwright.async_api import async_playwright, Page, Browser, TimeoutError as PWTimeout

# ===========================
# Konfiguration (ENV overrides)
# ===========================
DEF_MAX_PARALLEL       = int(os.getenv("AISO_MAX_PARALLEL", "12"))
DEF_HEADLESS           = os.getenv("AISO_HEADLESS", "true").lower() in ("1","true","yes")
DEF_INTERVAL_SEC       = int(os.getenv("AISO_INTERVAL_SEC", "30"))
DEF_MATCH_TIMEOUT_SEC  = int(os.getenv("AISO_MATCH_TIMEOUT_SEC", "120"))
DEF_BLOCK_RESOURCES    = os.getenv("AISO_BLOCK_RESOURCES", "true").lower() in ("1","true","yes")
DEF_FUZZY_THRESHOLD    = float(os.getenv("AISO_FUZZY_THRESHOLD", "0.72"))
DEF_SINGLE_TEAM_MATCH  = os.getenv("AISO_SINGLE_TEAM_MATCH", "1") in ("1","true","yes")
DEF_SINGLE_TEAM_THRESH = float(os.getenv("AISO_SINGLE_TEAM_THRESH", "0.85"))
DEF_SCAN_POLL_MS       = int(os.getenv("AISO_SCAN_POLL_MS", "250"))
DEF_SCROLL_STEP_PX     = int(os.getenv("AISO_SCROLL_STEP_PX", "320"))
DEF_SCROLL_PAUSE_MS    = int(os.getenv("AISO_SCROLL_PAUSE_MS", "450"))
DEF_SCROLL_IDLE_ROUNDS = int(os.getenv("AISO_SCROLL_IDLE_ROUNDS", "8"))
DEF_TIMEZONE           = os.getenv("TZ", "Europe/Berlin")
DEF_LOCALE             = os.getenv("LOCALE", "de-DE")
DEF_USER_AGENT         = os.getenv("AISO_UA", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                                 "AppleWebKit/537.36 (KHTML, like Gecko) "
                                                 "Chrome/123.0.0.0 Safari/537.36")
DEF_VIEWPORT_W         = int(os.getenv("AISO_VIEWPORT_W", "1600"))
DEF_VIEWPORT_H         = int(os.getenv("AISO_VIEWPORT_H", "1000"))
MATCH_DEBUG            = os.getenv("AISO_MATCH_DEBUG", "0") in ("1","true","yes")

def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ===========================
# Fuzzy Utils
# ===========================
_STOPWORDS = {
    "fc","cf","sc","afc","ac","as","bc","bk","fk","sk","kf","ks","od","nk","cd","sd","ss","ssc",
    "u","u17","u18","u19","u20","u21","u22","u23","b","ii","iii","2","team","club","deportivo",
    "atletico","athletic","real","sporting","univ","universidad","borussia","sv","tsv","ssv",
    "c.f.","s.c.","f.c.","s.s.","c.d.","c.a.","c.s.","cf.","sc.","fc.","sad","sp.","calcio",
    "city","united","utd"
}
_ALIAS = {
    "cote divoire": "ivory coast","cote d ivoire":"ivory coast","cote-d-ivoire":"ivory coast",
    "côte d ivoire":"ivory coast","korea republic":"south korea","republic of korea":"south korea",
    "korea dpr":"north korea","viet nam":"vietnam","u s a":"usa","u.s.a":"usa",
    "uae":"united arab emirates","u.a.e":"united arab emirates","dr congo":"congo dr",
    "congo drc":"congo dr","cape verde":"cabo verde","czechia":"czech republic",
    "fyrom":"north macedonia","bosnia herzegovina":"bosnia and herzegovina",
    "man utd":"manchester united","man united":"manchester united","man u":"manchester united",
    "man city":"manchester city","psg":"paris saint germain","inter":"inter milan",
    "bayern":"bayern munich","ath bilbao":"athletic bilbao",
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
    s = _clean(s); s = _ALIAS.get(s, s)
    tokens = [t for t in s.split() if t not in _STOPWORDS]
    if tokens and tokens[-1] in {"u19","u20","u21","u23","b","ii","iii","2"}:
        tokens = tokens[:-1]
    return " ".join(tokens).strip()
def fuzzy_score(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    ca, cb = canonical_team(a), canonical_team(b)
    r1 = SequenceMatcher(None, ca, cb).ratio()
    ta, tb = set(ca.split()), set(cb.split())
    r2 = (len(ta & tb) / len(ta | tb)) if (ta or tb) else 0.0
    return 0.6*r1 + 0.4*r2
def pair_scores(q_home: str, q_away: str, cand_home: str, cand_away: str):
    n_hh = fuzzy_score(q_home, cand_home); n_aa = fuzzy_score(q_away, cand_away)
    s_ha = fuzzy_score(q_home, cand_away); s_ah = fuzzy_score(q_away, cand_home)
    normal_pair  = min(n_hh, n_aa); swapped_pair = min(s_ha, s_ah)
    single_best  = max(n_hh, n_aa, s_ha, s_ah)
    return normal_pair, swapped_pair, single_best

# ===========================
# JS: Stats-Observer
# ===========================
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
      if(/finished|ft|ended|full\s*time/i.test(t)) return 100;
      const mmss = t.match(/(\d{1,2})\s*:\s*\d{2}/); if(mmss) return parseInt(mmss[1],10);
      const m = t.match(/(\d{1,3})\s*[’']/);        if(m)    return parseInt(m[1],10);
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
      const txt = side==="h" ? (q(root, ".ml-20.mr-xs") || q(root, ".mr-xs"))
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

  function endedFlag(){
    const el = document.querySelector(".match-status,.ai-match-status,.status,.time,.score-time,.status-time,.m-time,.timer,.matchTime");
    const t = el ? el.textContent.trim() : "";
    return /finished|ft|ended|full\s*time/i.test(t) ? 1 : 0;
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
      red_h:null, red_a:null,
      ended: endedFlag()
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

# ===========================
# Pool
# ===========================
class AiScoreWorkerPool:
    """
    Verwaltet bis zu max_parallel Scraper parallel.
    Aufgabe: {"match_id": int|str, "home": str, "away": str}
    Optional: on_insert(row) Async-Callback (DB), should_stop(task) -> bool
    """

    def __init__(
        self,
        max_parallel: int = DEF_MAX_PARALLEL,
        scrape_interval: int = DEF_INTERVAL_SEC,
        headless: bool = DEF_HEADLESS,
        on_insert: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        should_stop: Optional[Callable[[Dict[str, Any]], Awaitable[bool]]] = None,
    ):
        self.max_parallel = max_parallel
        self.scrape_interval = scrape_interval
        self.headless = headless
        self.on_insert_cb = on_insert
        self.should_stop_cb = should_stop
        self._sem = asyncio.Semaphore(self.max_parallel)
        self._browser: Optional[Browser] = None
        self._pw = None
        self._running: Dict[Any, asyncio.Task] = {}

        print(f"[AiScore] Worker v2.2 – FT-Check=ENABLED, interval={self.scrape_interval}s, max_parallel={self.max_parallel}")

    async def start(self):
        if self._browser:
            return
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox","--disable-dev-shm-usage",
                f"--window-size={DEF_VIEWPORT_W},{DEF_VIEWPORT_H}",
            ],
        )

    async def close(self):
        for mid, t in list(self._running.items()):
            if t and not t.done():
                t.cancel()
        self._running.clear()
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._pw:
            await self._pw.stop()
            self._pw = None

    def is_running(self, match_id: Any) -> bool:
        t = self._running.get(match_id)
        return bool(t and not t.done())

    def count_running(self) -> int:
        return sum(1 for t in self._running.values() if t and not t.done())

    async def submit(self, task: Dict[str, Any]):
        await self.start()
        mid = task.get("match_id")
        if self.is_running(mid):
            return
        coro = self._guarded_run_task(task)
        t = asyncio.create_task(coro, name=f"aiscore-{mid}")
        self._running[mid] = t

    async def _guarded_run_task(self, task: Dict[str, Any]):
        async with self._sem:
            try:
                await self._run_task(task)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                print(f"[AiScore] Task {task.get('match_id')} crashed: {e}")
            finally:
                self._running.pop(task.get("match_id"), None)

    # =============== Kern-Worker ===============
    async def _run_task(self, task: Dict[str, Any]):
        mid = task.get("match_id")
        home = task.get("home") or ""
        away = task.get("away") or ""
        if not (home or away):
            print(f"[{ts()}] [AiScore] {mid} skip: no team names provided")
            return

        ctx = await self._browser.new_context(
            locale=DEF_LOCALE, timezone_id=DEF_TIMEZONE,
            user_agent=DEF_USER_AGENT,
            viewport={"width": DEF_VIEWPORT_W, "height": DEF_VIEWPORT_H},
        )

        await ctx.add_init_script("""
          Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
          window.open = (url) => { if (url) location.href = url; return null; };
          document.addEventListener('click', (e) => {
            const a = e.target && e.target.closest && e.target.closest('a[target="_blank"]');
            if (a) a.setAttribute('target','_self');
          }, true);
        """)

        if DEF_BLOCK_RESOURCES:
            async def _route(r):
                if r.request.resource_type in {"image","media","font"}:
                    await r.abort()
                else:
                    await r.continue_()
            await ctx.route("**/*", _route)

        page = await ctx.new_page()
        try:
            await self._goto_live(page)

            # Match suchen
            found = await self._find_and_open_match(page, home, away, timeout_sec=DEF_MATCH_TIMEOUT_SEC)
            if not found:
                print(f"[{ts()}] [AiScore] {mid} ❌ Match nicht gefunden.")
                return

            # Stats hook
            await self._wait_stats_ready(page)
            await page.evaluate(MUTATION_OBSERVER_JS)

            # Warten bis erste sinnvolle Werte da sind
            ready = False
            for _ in range(20):
                snap = await page.evaluate("window.__bbStats || null")
                if snap and (snap.get("minute") is not None or snap.get("corners_h") is not None or snap.get("sog_h") is not None):
                    ready = True; break
                await asyncio.sleep(0.5)

            # Loop
            while True:
                if self.should_stop_cb and await self.should_stop_cb(task):
                    print(f"[{ts()}] [AiScore] {mid} externes Stop-Flag – beende.")
                    break

                snap = await page.evaluate("window.__bbStats || null")
                if snap:
                    await self._emit_row(task, snap)
                    if snap.get("ended") == 1 or (snap.get("minute") is not None and snap["minute"] >= 100):
                        print(f"[{ts()}] [AiScore] {mid} FT erkannt – stoppe.")
                        break

                await asyncio.sleep(DEF_INTERVAL_SEC)

        finally:
            await ctx.close()

    async def _emit_row(self, task: Dict[str, Any], snap: Dict[str, Any]):
        mid = task.get("match_id")
        row = {
            "match_id": mid, "ts_utc": ts(), "minute": snap.get("minute"),
            "possession_h": snap.get("possession_h"), "possession_a": snap.get("possession_a"),
            "attacks_h": snap.get("attacks_h"), "attacks_a": snap.get("attacks_a"),
            "dangerous_h": snap.get("dangerous_h"), "dangerous_a": snap.get("dangerous_a"),
            "shots_h": snap.get("shots_h"), "shots_a": snap.get("shots_a"),
            "sog_h": snap.get("sog_h"), "sog_a": snap.get("sog_a"),
            "soff_h": snap.get("soff_h"), "soff_a": snap.get("soff_a"),
            "corners_h": snap.get("corners_h"), "corners_a": snap.get("corners_a"),
            "yellow_h": snap.get("yellow_h"), "yellow_a": snap.get("yellow_a"),
            "red_h": snap.get("red_h"), "red_a": snap.get("red_a"),
        }
        if self.on_insert_cb:
            await self.on_insert_cb(row)
        else:
            m = row.get("minute")
            print(f"[AiScore] {mid} insert ok (min {m if m is not None else 'None'})")

    # =============== Navigation & Suche ===============
    async def _goto_live(self, page: Page):
        try:
            await page.goto("https://www.aiscore.com/", wait_until="domcontentloaded", timeout=30000)
            try:
                await page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
        except PWTimeout:
            pass

    async def _wait_stats_ready(self, page: Page):
        try:
            await page.wait_for_selector(".stats2.w-bar-100, .ai-statistics, .statistics, .match-statistics, .stats", timeout=20000)
        except Exception:
            pass

    async def _extract_card_teams(self, a_el) -> Tuple[str, str]:
        try:
            ht = await a_el.locator("span[itemprop='homeTeam']").inner_text(timeout=300)
            at = await a_el.locator("span[itemprop='awayTeam']").inner_text(timeout=300)
            if ht and at: return ht.strip(), at.strip()
        except Exception:
            pass
        for hs, asel in [
            (".teamHomeBox .nameBox", ".teamAwayBox .nameBox"),
            (".home .nameBox", ".away .nameBox"),
            (".home .name", ".away .name"),
        ]:
            try:
                ht = await a_el.locator(hs).inner_text(timeout=250)
                at = await a_el.locator(asel).inner_text(timeout=250)
                if ht and at: return ht.strip(), at.strip()
            except Exception:
                pass
        try:
            raw = await a_el.inner_text(timeout=200)
            raw = re.sub(r"\s+", " ", raw.strip())
            parts = re.split(r"\s+vs\s+|\s+-\s+|\s+—\s+|\s+v\s+", raw, flags=re.I)
            if len(parts) >= 2: return parts[0].strip(), parts[1].strip()
        except Exception:
            pass
        return "", ""

    async def _scroll_to_load_all(self, page: Page, max_ms: int = 20000, step: int = DEF_SCROLL_STEP_PX, pause_ms: int = DEF_SCROLL_PAUSE_MS):
        start = time.time()
        last_h = -1
        stable = 0
        while (time.time() - start) * 1000 < max_ms:
            try: await page.evaluate(f"window.scrollBy(0, {step})")
            except Exception: pass
            await asyncio.sleep(pause_ms/1000)
            try: h = await page.evaluate("document.scrollingElement.scrollHeight")
            except Exception: h = last_h
            if h == last_h: stable += 1
            else: stable = 0; last_h = h
            if stable >= 3: break
        try: await page.evaluate("window.scrollTo(0,0)")
        except Exception: pass

    async def _active_scan_with_scroll(self, page: Page, home: str, away: str, max_sec: int) -> Optional[str]:
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
                ht, at = await self._extract_card_teams(a_el)
                if not ht or not at:
                    continue
                n_pair, s_pair, single_best = pair_scores(home, away, ht, at)
                pair_best = max(n_pair, s_pair)

                if pair_best >= DEF_FUZZY_THRESHOLD:
                    href = await a_el.get_attribute("href")
                    if href and href.startswith("/"): href = "https://www.aiscore.com"+href
                    print(f"[{ts()}] Treffer: {canonical_team(ht)} vs {canonical_team(at)} -> {href}")
                    return href

                if DEF_SINGLE_TEAM_MATCH and single_best >= DEF_SINGLE_TEAM_THRESH:
                    href = await a_el.get_attribute("href")
                    if href and href.startswith("/"): href = "https://www.aiscore.com"+href
                    if (single_best > best["single"]) or (abs(single_best - best["single"]) < 1e-6 and pair_best > best["pair"]):
                        best = {"href": href, "pair": pair_best, "single": single_best}

            if best["href"]:
                print(f"[{ts()}] 1-Team-Treffer -> {best['href']}")
                return best["href"]
            return None

        await self._scroll_to_load_all(page, max_ms=20000)

        while time.time() - start < max_sec:
            href = await scan_once()
            if href:
                return href

            if tick % max(1, DEF_SCROLL_PAUSE_MS // DEF_SCAN_POLL_MS) == 0:
                try: await page.evaluate(f"window.scrollBy(0, {DEF_SCROLL_STEP_PX})")
                except Exception: pass
                await asyncio.sleep(DEF_SCROLL_PAUSE_MS / 1000.0)

            if idle_rounds >= DEF_SCROLL_IDLE_ROUNDS:
                break

            await asyncio.sleep(DEF_SCAN_POLL_MS / 1000.0)
            tick += 1

        return None

    async def _find_and_open_match(self, page: Page, home: str, away: str, timeout_sec: int) -> bool:
        try:
            await page.wait_for_selector("a.match-container, a[href*='/match-']", timeout=10000)
        except Exception:
            pass
        href = await self._active_scan_with_scroll(page, home, away, max_sec=timeout_sec)
        if href:
            await page.goto(href, wait_until="domcontentloaded")
            return True
        return False