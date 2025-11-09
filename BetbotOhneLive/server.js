/**
 * BetBro Mini-Dashboard – DE
 * - Heutige Fixtures, Predictions (Queue+Retry) & Odds (Pagination)
 * - Heavy (Predictions+Odds) bis Mitternacht gecacht; Status (Fixtures) bis zum nächsten 15-Min-Takt
 * - UI: Tabs Heute (Live + Demnächst), Top (große Ligen), Vergangen, Favoriten, Top Picks
 * - Striktes Filtern: nur Spiele mit valider 1X2-Prediction (keine „No predictions available“)
 * - Top-Ligen: IDs werden 1x/Tag dynamisch aus /leagues ermittelt (Name+Land-Match). Top-Spiele erscheinen in Hauptlisten UND im Top-Tab.
 * - Top-Picks (unabhängig von Top-Ligen): Score = 0.5*PredictionScore + 0.3*FormScore + 0.2*TableScore
 */

const express = require("express");
const axios = require("axios");

const PORT = process.env.PORT || 5010;
const API_KEY = process.env.API_FOOTBALL_KEY || "f8be7402447010e1c3a4b67ee8883e56";
const TIMEZONE = "Europe/Berlin";

/* -------- Einstellungen -------- */
const USE_LIVE_FOR_PICKS = false; // Top-Picks nur Upcoming (Pre-Match)

/* -------------------------------- */

const API = axios.create({
  baseURL: "https://v3.football.api-sports.io",
  headers: { "x-apisports-key": API_KEY },
  timeout: 30000,
});

const app = express();
const cache = new Map();
const setCache = (k, v, ttlMs) => cache.set(k, { v, e: Date.now() + ttlMs });
const getCache = (k) => {
  const it = cache.get(k);
  if (!it) return null;
  if (Date.now() > it.e) { cache.delete(k); return null; }
  return it.v;
};

function ttlToNextQuarter() {
  const now = new Date();
  const m = now.getMinutes();
  const next = new Date(now);
  const q = [0, 15, 30, 45].find((mm) => mm > m);
  if (q === undefined) next.setHours(now.getHours() + 1, 0, 0, 0);
  else next.setMinutes(q, 0, 0);
  return next - now;
}
function ttlToMidnight() {
  const now = new Date();
  const mid = new Date(now);
  mid.setHours(24, 0, 0, 0);
  return mid - now;
}
const todayISO = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
};

/* ---------- Loaders ---------- */
async function loadFixtures(date) {
  const fx = await API.get("/fixtures", { params: { date, timezone: TIMEZONE } });
  return fx.data?.response || [];
}
async function loadAllOdds(date) {
  const all = {};
  let page = 1, total = 1;
  try {
    while (page <= total) {
      const od = await API.get("/odds", { params: { date, timezone: TIMEZONE, page } });
      total = od.data?.paging?.total || 1;
      for (const item of od.data?.response || []) {
        const fid = item.fixture?.id;
        if (!fid) continue;
        let picked = null;
        for (const bm of item.bookmakers || []) {
          for (const bet of bm.bets || []) {
            const name = (bet.name || "").toLowerCase();
            if (!(name.includes("1x2") || name.includes("match winner"))) continue;
            const vals = {};
            for (const v of bet.values || []) {
              const label = (v.value || "").toLowerCase();
              if (label.includes("home") || label === "1") vals.home = v.odd;
              if (label.includes("draw") || label === "x") vals.draw = v.odd;
              if (label.includes("away") || label === "2") vals.away = v.odd;
            }
            if (vals.home && vals.draw && vals.away) { picked = vals; break; }
          }
          if (picked) break;
        }
        if (picked) all[fid] = picked;
      }
      page++;
      await new Promise((r) => setTimeout(r, 320));
    }
  } catch (e) {
    console.warn("Odds-Fehler:", e.message);
  }
  return all;
}
async function loadPredictions(fixtures) {
  const out = [];
  const jobs = fixtures.map((f) => async () => {
    const fid = f.fixture?.id;
    let prediction = null;
    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        const pr = await API.get("/predictions", { params: { fixture: fid } });
        prediction = pr.data?.response?.[0] || null;
        break;
      } catch (e) {
        const wait = e.response?.status === 429 ? 2000 : 600;
        await new Promise((r) => setTimeout(r, wait));
      }
    }
    out.push({ fid, prediction });
    await new Promise((r) => setTimeout(r, 200));
  });
  async function run(limit = 3) {
    const running = new Set();
    for (const job of jobs) {
      const p = job().finally(() => running.delete(p));
      running.add(p);
      if (running.size >= limit) await Promise.race(running);
    }
    await Promise.all([...running]);
  }
  await run(3);
  return out;
}

/* ---------- Leagues → Top-IDs (dynamisch) ---------- */
async function loadLeagues() {
  const cacheKey = "leagues:all";
  const cached = getCache(cacheKey);
  if (cached) return cached;
  try {
    const r = await API.get("/leagues");
    const leagues = r.data?.response || [];
    setCache(cacheKey, leagues, ttlToMidnight());
    return leagues;
  } catch (e) {
    console.warn("Leagues-Fehler:", e.message);
    return [];
  }
}
const TOP_RULES = [
  // Kontinentale Klub-Wettbewerbe
  { countryAnyOf: ["world","europe"], nameIncludes: ["uefa champions league"] },
  { countryAnyOf: ["world","europe"], nameIncludes: ["uefa europa league"] },
  { countryAnyOf: ["world","europe"], nameIncludes: ["uefa europa conference league"] },

  // Nationalmannschaften (gewünscht)
  { countryAnyOf: ["world","europe"], nameIncludes: ["euro championship","uefa euro"] },
  { countryAnyOf: ["world","europe"], nameIncludes: ["uefa nations league"] },
  { countryAnyOf: ["world","south america"], nameIncludes: ["copa america"] },
  { countryAnyOf: ["world"], nameIncludes: ["world cup"] },

  // Top Ligen (National) – inkl. deutsche 2. Liga
  { country: "england", nameIncludes: ["premier league"] },
  { country: "spain",   nameIncludes: ["la liga","laliga"] },
  { country: "italy",   nameIncludes: ["serie a"] },
  { country: "germany", nameIncludes: ["bundesliga"], notIncludes: ["2."] }, // 1. Bundesliga
  { country: "germany", nameIncludes: ["2. bundesliga","2 bundesliga","2-bundesliga"] }, // 2. BL
  { country: "france",  nameIncludes: ["ligue 1"] },
  { country: "netherlands", nameIncludes: ["eredivisie"] },
  { country: "portugal", nameIncludes: ["primeira liga","liga portugal"] },

  // Nationale Pokale
  { country: "germany", nameIncludes: ["dfb-pokal","dfb pokal"] },
  { country: "england", nameIncludes: ["fa cup"] },
  { country: "england", nameIncludes: ["efl cup","league cup","carabao cup"] },
  { country: "spain",   nameIncludes: ["copa del rey"] },
  { country: "italy",   nameIncludes: ["coppa italia"] },
  { country: "france",  nameIncludes: ["coupe de france"] },
  { country: "portugal",nameIncludes: ["taça de portugal","taca de portugal","taca portugal"] },
  { country: "netherlands", nameIncludes: ["knvb beker"] },
];
const safe = (s) => (s || "").toString().trim().toLowerCase();
function matchLeagueToRules(entry) {
  const name = safe(entry?.league?.name);
  const country = safe(entry?.country?.name);
  for (const r of TOP_RULES) {
    const nameHit = (r.nameIncludes || []).some(n => name.includes(n));
    if (!nameHit) continue;
    if (r.notIncludes && r.notIncludes.some(n => name.includes(n))) continue;
    if (r.countryAnyOf && r.countryAnyOf.includes(country)) return true;
    if (r.country && r.country === country) return true;
  }
  return false;
}
let TOP_LEAGUE_IDS = new Set();
async function ensureTopIdsReady() {
  if (TOP_LEAGUE_IDS.size > 0) return TOP_LEAGUE_IDS;
  const leagues = await loadLeagues();
  const ids = new Set();
  for (const l of leagues) {
    try { if (matchLeagueToRules(l) && l.league?.id) ids.add(l.league.id); } catch {}
  }
  TOP_LEAGUE_IDS = ids;
  return TOP_LEAGUE_IDS;
}
function isTopLeagueByIds(lg = {}) {
  const id = lg?.id || lg?.league?.id;
  if (!id) return false;
  return TOP_LEAGUE_IDS.has(id);
}

/* ---------- Prediction helpers ---------- */
function normalizeName(s) {
  return String(s || "")
    .toLowerCase()
    .replace(/\b(fc|sc|ac|cf|cd|afc|utd|united|city|club|sv|ss|as)\b/g, "")
    .replace(/\s+/g, "")
    .replace(/[^a-z0-9]/g, "");
}
function hasValidPrediction(pred) {
  if (!pred || !pred.predictions) return false;
  const p = pred.predictions;
  if (p.winner?.name) return true;
  const H = parseInt((p.percent?.home || "0").replace("%", "")) || 0;
  const D = parseInt((p.percent?.draw || "0").replace("%", "")) || 0;
  const A = parseInt((p.percent?.away || "0").replace("%", "")) || 0;
  const max = Math.max(H, D, A);
  const winners = [H === max, D === max, A === max].filter(Boolean).length;
  return max > 0 && winners === 1;
}
function outcomeFromPrediction(pred, homeName, awayName) {
  if (!hasValidPrediction(pred)) return null;
  const p = pred.predictions;
  if (p.winner?.name) {
    const w = normalizeName(p.winner.name);
    if (["draw", "unentschieden"].includes(w)) return "D";
    const h = normalizeName(homeName), a = normalizeName(awayName);
    if (w && (w === h || h.includes(w) || w.includes(h))) return "H";
    if (w && (w === a || a.includes(w) || w.includes(a))) return "A";
  }
  const H = parseInt((p.percent?.home || "0").replace("%", "")) || 0;
  const D = parseInt((p.percent?.draw || "0").replace("%", "")) || 0;
  const A = parseInt((p.percent?.away || "0").replace("%", "")) || 0;
  const max = Math.max(H, D, A);
  const list = [];
  if (H === max) list.push("H");
  if (D === max) list.push("D");
  if (A === max) list.push("A");
  return list.length === 1 ? list[0] : null;
}

/* ---------- Top-Picks: deine 3-Merkmale-Formel ---------- */
function toNum(x){ const n = typeof x === "string" ? parseFloat(x.replace(",",".")) : x; return Number.isFinite(n)?n:null; }
function pctNum(s){ if(s==null) return 0; if(typeof s==='string' && s.endsWith('%')) return parseInt(s)||0; return +s||0; }

/**
 * Hilfsfunktion: Tabellenränge aus prediction.league.standings extrahieren.
 * API liefert oft [[...]]; wir flatten und matchen über Teamnamen.
 */
function extractRanks(item){
  const standings = item.prediction?.league?.standings;
  if (!Array.isArray(standings)) return { home: null, away: null };
  const flat = standings.flat();
  const homeName = normalizeName(item.fixture?.teams?.home?.name);
  const awayName = normalizeName(item.fixture?.teams?.away?.name);
  let homeRank = null, awayRank = null;
  for (const row of flat) {
    const n = normalizeName(row.team?.name);
    if (!n) continue;
    if (homeRank === null && n === homeName) homeRank = row.rank ?? null;
    if (awayRank === null && n === awayName) awayRank = row.rank ?? null;
    if (homeRank !== null && awayRank !== null) break;
  }
  return { home: homeRank, away: awayRank };
}

/**
 * Berechnet TopPick: nur auf Basis
 * - PredictionScore (Prozent der favorisierten Seite)
 * - FormScore (|Form(Home) - Form(Away)|)
 * - TableScore (|RankDiff|)
 * Keine Quoten, keine Edge-Gates.
 */
function computeTopPick(item){
  const pred = item.prediction?.predictions;
  if(!pred) return null;

  // PredictionScore
  const perc = pred.percent || {};
  const H = pctNum(perc.home), D = pctNum(perc.draw), A = pctNum(perc.away);
  const cand = [{key:'H',val:H},{key:'D',val:D},{key:'A',val:A}].sort((a,b)=>b.val-a.val);
  const best = cand[0];
  const PredictionScore = best.val; // 0..100

  // FormScore
  const formH = pctNum(item.prediction?.teams?.home?.last_5?.form);
  const formA = pctNum(item.prediction?.teams?.away?.last_5?.form);
  const FormScore = Math.abs(formH - formA); // 0..100

  // TableScore
  let TableScore = 0;
  const { home: rH, away: rA } = extractRanks(item);
  if (rH != null && rA != null) {
    TableScore = Math.abs(rH - rA); // 0..(Liga-Teams-1)
  }

  // Gesamt
  const TotalScore = Math.round(
    0.5 * PredictionScore +
    0.3 * FormScore +
    0.2 * TableScore
  );

  return {
    market: "1X2",
    selection: best.key,                 // "H" | "D" | "A"
    predictionScore: PredictionScore,    // %
    formScore: FormScore,                // Δ%
    tableScore: TableScore,              // Δ Plätze
    score: TotalScore,                   // 0.. ~100+
    explain: { PredictionScore, FormScore, TableScore }
  };
}

/* ---------- API ---------- */
app.get("/api/fixtures", async (req, res) => {
  try {
    const date = (req.query.date || todayISO()).slice(0, 10);

    await ensureTopIdsReady();

    const statusKey = `status:${date}`;
    let fixtures = getCache(statusKey);
    if (!fixtures) {
      fixtures = await loadFixtures(date);
      setCache(statusKey, fixtures, ttlToNextQuarter());
    }

    const heavyKey = `heavy:${date}`;
    let heavy = getCache(heavyKey);
    if (!heavy) {
      const base = fixtures.length ? fixtures : await loadFixtures(date);
      const [oddsMap, preds] = await Promise.all([loadAllOdds(date), loadPredictions(base)]);
      const predMap = {};
      for (const p of preds) predMap[p.fid] = p.prediction;
      heavy = { oddsMap, predMap };
      setCache(heavyKey, heavy, ttlToMidnight());
    }

    const now = Date.now() / 1000;
    const liveCodes = new Set(["1H", "2H", "ET", "BT", "HT", "P", "LIVE"]);
    const finishedCodes = new Set(["FT", "AET", "PEN", "ABD", "AWD", "WO"]);
    const nsCode = "NS";

    const itemsRaw = fixtures.map((f) => {
      const fid = f.fixture?.id;
      const prediction = heavy.predMap[fid] || null;
      const odds = heavy.oddsMap[fid] || null;

      const sc = f.score?.fulltime || {};
      const gh = Number.isFinite(sc.home) ? sc.home : f.goals?.home;
      const ga = Number.isFinite(sc.away) ? sc.away : f.goals?.away;

      let evaluation = null;
      const short = f.fixture?.status?.short || "";
      if (finishedCodes.has(short) && hasValidPrediction(prediction)) {
        const pred = outcomeFromPrediction(prediction, f.teams?.home?.name, f.teams?.away?.name);
        if (pred) {
          let act = null;
          if (typeof gh === "number" && typeof ga === "number") act = gh > ga ? "H" : gh < ga ? "A" : "D";
          else if (f.teams?.home?.winner === true) act = "H";
          else if (f.teams?.away?.winner === true) act = "A";
          else if (f.teams?.home?.winner === false && f.teams?.away?.winner === false) act = "D";
          if (act) evaluation = pred === act ? "correct" : "wrong";
        }
      }

      return {
        fixture: f,
        prediction,
        odds,
        result: (typeof gh === "number" && typeof ga === "number") ? { home: gh, away: ga } : null,
        evaluation,
        hasValid: hasValidPrediction(prediction),
        isTop: isTopLeagueByIds(f.league || {}),
      };
    });

    // striktes Filtern auf gültige Predictions
    const items = itemsRaw.filter(x => x.hasValid);

    const live = [], upcoming = [], past = [];
    const liveTop = [], upcomingTop = [], pastTop = [];
    const picks = [];

    for (const it of items) {
      const short = it.fixture.fixture?.status?.short || "";
      const ts = it.fixture.fixture?.timestamp || 0;
      const bucket = liveCodes.has(short) ? "live" : (short === nsCode || ts >= now) ? "upcoming" : "past";

      if (bucket === "live") live.push(it);
      if (bucket === "upcoming") upcoming.push(it);
      if (bucket === "past") past.push(it);

      if (it.isTop) {
        if (bucket === "live") liveTop.push(it);
        if (bucket === "upcoming") upcomingTop.push(it);
        if (bucket === "past") pastTop.push(it);
      }

      // Top Picks (unabhängig von Top-Ligen), nach neuer Formel:
      const allow = (bucket==='upcoming') || (USE_LIVE_FOR_PICKS && bucket==='live');
      if (allow) {
        const pick = computeTopPick(it);
        if (pick) { it.pick = pick; picks.push(it); }
      }
    }

    const sortAsc = (a,b)=>a.fixture.fixture.timestamp - b.fixture.fixture.timestamp;
    const sortDesc = (a,b)=>b.fixture.fixture.timestamp - a.fixture.fixture.timestamp;
    live.sort(sortDesc); upcoming.sort(sortAsc); past.sort(sortDesc);
    liveTop.sort(sortDesc); upcomingTop.sort(sortAsc); pastTop.sort(sortDesc);

    // Picks sortieren: Score ↓, PredictionScore ↓, Anstoß ↑
    picks.sort((a,b)=>{
      const sa = a.pick?.score ?? 0, sb = b.pick?.score ?? 0;
      if (sb !== sa) return sb - sa;
      const pa = a.pick?.predictionScore ?? 0, pb = b.pick?.predictionScore ?? 0;
      if (pb !== pa) return pb - pa;
      return (a.fixture.fixture.timestamp - b.fixture.fixture.timestamp);
    });

    res.json({
      date,
      sections: {
        today: { live, upcoming },
        top:   { live: liveTop, upcoming: upcomingTop, past: pastTop },
        past,
        picks
      }
    });
  } catch (e) {
    console.error(e);
    res.status(500).json({ error: "Fehler beim Laden der Daten." });
  }
});

/* ---------- UI ---------- */
app.get("/", (req, res) => {
  res.send(`<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>BetBro Dashboard</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  body{background:#0D1117;color:white;}
  .section-title{border-left:3px solid #38BDF8;padding-left:.5rem;margin:.75rem 0 .5rem 0;}
  .card-hover:hover{transform:translateY(-2px);box-shadow:0 2px 6px rgba(0,0,0,.3);}
  .pill{border:1px solid #7f1d1d;background:#7f1d1d22;color:#fca5a5;border-radius:9999px;padding:.1rem .5rem;font-size:.7rem;}
  .pickpill{border:1px solid #065f46;background:#065f4622;color:#86efac;border-radius:9999px;padding:.1rem .5rem;font-size:.7rem;}
  .track{height:12px;background:#0f172a;border:1px solid #1f2937;border-radius:10px;position:relative;overflow:hidden}
  .leftbar{position:absolute;left:0;top:0;bottom:0;background:#3b82f6}
  .rightbar{position:absolute;right:0;top:0;bottom:0;background:#22c55e}
  .fav-ring{box-shadow:0 0 0 2px rgba(250,204,21,.55);}
  dialog::backdrop{backdrop-filter:blur(6px);background:rgba(0,0,0,.45);}
  .eval-icon{font-size:18px;line-height:18px}
</style>
</head>
<body class="px-4 py-4">
  <div class="w-full max-w-screen-2xl mx-auto">
    <div class="flex flex-col sm:flex-row justify-between items-center gap-3 mb-3">
      <h1 class="text-xl font-semibold">⚽ BetBro Dashboard</h1>
      <div class="flex items-center gap-2">
        <input id="date" type="date" class="bg-[#161B22] border border-[#21262d] rounded px-2 py-1">
        <button id="btnLoad" class="bg-cyan-500 text-black px-3 py-1 rounded font-medium">Neu laden</button>
      </div>
    </div>

    <!-- Tabs -->
    <div class="flex gap-2 mb-3 flex-wrap">
      <button id="tabToday" class="border border-[#1f2937] rounded px-3 py-1 bg-cyan-500 text-black">Heute</button>
      <button id="tabTop"   class="border border-[#1f2937] rounded px-3 py-1">Top</button>
      <button id="tabPicks" class="border border-[#1f2937] rounded px-3 py-1">Top Picks</button>
      <button id="tabPast"  class="border border-[#1f2937] rounded px-3 py-1">Vergangen</button>
      <button id="tabFavs"  class="border border-[#1f2937] rounded px-3 py-1">Favoriten</button>
    </div>

    <div id="stats" class="text-sm text-gray-300 mb-2"></div>

    <!-- Heute -->
    <div id="paneToday">
      <details id="liveBox" open class="mb-2">
        <summary class="section-title flex items-center justify-between select-none">
          <span>Live</span><span class="text-xs text-gray-400">(einklappen)</span>
        </summary>
        <div id="gridLive" class="grid gap-4 grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4"></div>
      </details>

      <h2 class="section-title">Demnächst</h2>
      <div id="gridUpcoming" class="grid gap-4 grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4"></div>
    </div>

    <!-- Top -->
    <div id="paneTop" class="hidden">
      <h2 class="section-title">Top – Live</h2>
      <div id="gridTopLive" class="grid gap-4 grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4"></div>
      <h2 class="section-title mt-2">Top – Demnächst</h2>
      <div id="gridTopUpcoming" class="grid gap-4 grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4"></div>
      <h2 class="section-title mt-2">Top – Vergangen</h2>
      <div id="gridTopPast" class="grid gap-4 grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4"></div>
    </div>

    <!-- Top Picks -->
    <div id="panePicks" class="hidden">
      <h2 class="section-title">Top Picks (automatisch)</h2>
      <div id="gridPicks" class="grid gap-4 grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4"></div>
    </div>

    <!-- Vergangen -->
    <div id="panePast" class="hidden">
      <div id="pastSummary" class="text-sm text-gray-300 mb-2"></div>
      <div id="gridPast" class="grid gap-4 grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4"></div>
    </div>

    <!-- Favoriten -->
    <div id="paneFavs" class="hidden">
      <div id="gridFavs" class="grid gap-4 grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4"></div>
    </div>
  </div>

  <!-- Details -->
  <dialog id="details" class="rounded-xl overflow-auto w-[min(1000px,95vw)] max-h-[90vh] p-0 border border-[#1f2937] shadow-xl">
    <div class="bg-[#0B1220] text-white p-4">
      <div class="flex justify-between items-center border-b border-[#1f2937] pb-2 mb-3">
        <h2 class="font-semibold">Vorhersage-Details</h2>
        <button onclick="dlg.close()" class="text-gray-300 hover:text-white">✕</button>
      </div>
      <div id="detailsBody" class="space-y-3 text-sm"></div>
    </div>
  </dialog>

  <script>
    const dateEl = document.getElementById('date');
    const btnLoad = document.getElementById('btnLoad');
    const stats = document.getElementById('stats');
    const dlg = document.getElementById('details');
    const detailsBody = document.getElementById('detailsBody');

    const paneToday = document.getElementById('paneToday');
    const paneTop   = document.getElementById('paneTop');
    const panePicks = document.getElementById('panePicks');
    const panePast  = document.getElementById('panePast');
    const paneFavs  = document.getElementById('paneFavs');

    const tabToday  = document.getElementById('tabToday');
    const tabTop    = document.getElementById('tabTop');
    const tabPicks  = document.getElementById('tabPicks');
    const tabPast   = document.getElementById('tabPast');
    const tabFavs   = document.getElementById('tabFavs');

    const pastSummary = document.getElementById('pastSummary');

    const gridLive = document.getElementById('gridLive');
    const gridUpcoming = document.getElementById('gridUpcoming');
    const gridTopLive = document.getElementById('gridTopLive');
    const gridTopUpcoming = document.getElementById('gridTopUpcoming');
    const gridTopPast = document.getElementById('gridTopPast');
    const gridPicks = document.getElementById('gridPicks');
    const gridPast = document.getElementById('gridPast');
    const gridFavs = document.getElementById('gridFavs');

    let data = null, dataDate = null;

    (function(){ const d=new Date(); dateEl.value=d.toISOString().split('T')[0]; })();
    btnLoad.onclick = () => load(dateEl.value);

    tabToday.onclick = () => switchTab('today');
    tabTop.onclick   = () => switchTab('top');
    tabPicks.onclick = () => switchTab('picks');
    tabPast.onclick  = () => { switchTab('past'); updatePastSummary(); };
    tabFavs.onclick  = () => switchTab('favs');

    function switchTab(name){
      const tabs = {
        today:[paneToday,tabToday],
        top:[paneTop,tabTop],
        picks:[panePicks,tabPicks],
        past:[panePast,tabPast],
        favs:[paneFavs,tabFavs]
      };
      for(const [k,[pane,btn]] of Object.entries(tabs)){
        if(k===name){ pane.classList.remove('hidden'); btn.classList.add('bg-cyan-500','text-black'); }
        else{ pane.classList.add('hidden'); btn.classList.remove('bg-cyan-500','text-black'); }
      }
      if(name==='favs') renderFavs();
    }

    load(dateEl.value);
    scheduleQuarterReload();
    function scheduleQuarterReload(){
      const now=new Date();
      const mins=now.getMinutes(), secs=now.getSeconds(), ms=now.getMilliseconds();
      const next=[0,15,30,45].find(m=>m>mins);
      let delay;
      if(next===undefined){ delay=(60-mins)*60*1000 - secs*1000 - ms; }
      else { delay=(next-mins)*60*1000 - secs*1000 - ms; }
      setTimeout(()=>{ load(dateEl.value); setInterval(()=>load(dateEl.value), 15*60*1000); }, Math.max(200, delay));
    }

    async function load(date){
      stats.textContent='Lade...';
      const r=await fetch('/api/fixtures?date='+encodeURIComponent(date));
      data=await r.json(); dataDate=data.date;

      const total = (data.sections.today.live.length + data.sections.today.upcoming.length +
                     data.sections.top.live.length + data.sections.top.upcoming.length + data.sections.top.past.length +
                     data.sections.picks.length + data.sections.past.length);
      stats.textContent = 'Datum: '+data.date+' • Spiele: '+total+' • Top Picks: '+data.sections.picks.length;

      renderSection(gridLive, data.sections.today.live, 'live');
      renderSection(gridUpcoming, data.sections.today.upcoming, 'upcoming');
      renderSection(gridTopLive, data.sections.top.live, 'live');
      renderSection(gridTopUpcoming, data.sections.top.upcoming, 'upcoming');
      renderSection(gridTopPast, data.sections.top.past, 'past');
      renderSection(gridPicks, data.sections.picks, 'pick');
      renderSection(gridPast, data.sections.past, 'past');
      renderFavs();
      updatePastSummary();
    }

    function safeImg(src, cls, alt=''){ if(!src) return ''; return '<img src="'+src+'" alt="'+alt+'" referrerpolicy="no-referrer" loading="lazy" onerror="this.style.display=\\'none\\'" class="'+cls+'">'; }

    /* ---- Favoriten ---- */
    function favIds(){ return JSON.parse(localStorage.getItem('bb_favs') || '[]'); }
    function isFav(id){ return favIds().includes(id); }
    function toggleFav(id){
      const arr=favIds(); const i=arr.indexOf(id);
      if(i>=0) arr.splice(i,1); else arr.push(id);
      localStorage.setItem('bb_favs', JSON.stringify(arr));
      rerenderAll();
    }

    /* ---- Overrides (manuelle Korrektur) ---- */
    function ovKey(){ return 'bb_override_'+(dataDate||dateEl.value); }  // pro Datum
    function getOverrides(){ return JSON.parse(localStorage.getItem(ovKey())||'{}'); }
    function setOverride(fid, val){ const m=getOverrides(); if(val) m[fid]=val; else delete m[fid]; localStorage.setItem(ovKey(), JSON.stringify(m)); rerenderAll(); }
    function effectiveEval(item){
      const fid=item.fixture.fixture.id;
      const m=getOverrides();
      if(m[fid]==='correct') return 'correct';
      if(m[fid]==='wrong') return 'wrong';
      return item.evaluation||null;
    }

    function rerenderAll(){
      renderSection(gridLive, data.sections.today.live, 'live');
      renderSection(gridUpcoming, data.sections.today.upcoming, 'upcoming');
      renderSection(gridTopLive, data.sections.top.live, 'live');
      renderSection(gridTopUpcoming, data.sections.top.upcoming, 'upcoming');
      renderSection(gridTopPast, data.sections.top.past, 'past');
      renderSection(gridPicks, data.sections.picks, 'pick');
      renderSection(gridPast, data.sections.past, 'past');
      renderFavs();
      updatePastSummary();
    }

    /* ---- Favoriten-Ansicht ---- */
    function renderFavs(){
      if(!data){ gridFavs.innerHTML=''; return; }
      const mapById={};
      const all=[...data.sections.today.live, ...data.sections.today.upcoming,
                 ...data.sections.top.live, ...data.sections.top.upcoming, ...data.sections.top.past,
                 ...data.sections.picks, ...data.sections.past];
      for(const it of all) mapById[it.fixture.fixture.id]=it;
      const items=favIds().map(id=>mapById[id]).filter(Boolean);
      gridFavs.innerHTML='';
      if(!items.length){ gridFavs.innerHTML='<div class="text-gray-500">Noch keine Favoriten.</div>'; return; }
      for(const it of items) gridFavs.appendChild(makeCard(it, inferType(it)));
    }

    function inferType(it){
      const short=it.fixture.fixture?.status?.short||'';
      const liveCodes=new Set(["1H","2H","ET","BT","HT","P","LIVE"]);
      const now=Date.now()/1000; const ts=it.fixture.fixture?.timestamp||0;
      if(liveCodes.has(short)) return 'live';
      if(short==='NS' || ts>=now) return 'upcoming';
      return 'past';
    }

    function renderSection(container, list, type){
      container.innerHTML='';
      if(!list||!list.length){ container.innerHTML='<div class="text-gray-500">Keine Spiele.</div>'; return; }
      for(const it of list) container.appendChild(makeCard(it, type));
    }

    function makeCard(it, type){
      const f=it.fixture||{}; const fx=f.fixture||{}; const lg=f.league||{}; const t=f.teams||{}; const h=t.home||{}; const a=t.away||{};
      const d=fx.date?new Date(fx.date):null;
      const short=fx.status?.short||''; const elapsed=fx.status?.elapsed;
      const odds=it.odds; const res=it.result; const fid=fx.id;

      const card=document.createElement('div');
      card.className='text-left bg-[#161B22] border border-[#1f2937] rounded-2xl p-3 card-hover '+(isFav(fid)?'fav-ring':'');

      const liveLabel = (type==='live') ? '<span class="pill">LIVE '+(short==='HT'?'HT':(elapsed?(elapsed+"'"):(short||'')))+'</span>' : '';

      // Teamzeile (Live: Score neben Namen)
      let teamRow='';
      if(type==='live'){
        const gh = (typeof f.goals?.home==="number")? f.goals.home : '-';
        const ga = (typeof f.goals?.away==="number")? f.goals.away : '-';
        teamRow = \`
          <div class='flex justify-between items-center gap-2'>
            <div class='flex items-center gap-2 max-w-[45%]'>
              \${safeImg(h.logo,'w-6 h-6 rounded-full','home')}
              <span class='truncate'>\${h.name||''}</span>
              <span class='ml-1 font-semibold'>\${gh}</span>
            </div>
            <span class='text-gray-500 text-sm'>:</span>
            <div class='flex items-center gap-2 max-w-[45%] justify-end'>
              <span class='mr-1 font-semibold'>\${ga}</span>
              <span class='truncate text-right'>\${a.name||''}</span>
              \${safeImg(a.logo,'w-6 h-6 rounded-full','away')}
            </div>
          </div>\`;
      } else {
        teamRow = \`
          <div class='flex justify-between items-center gap-2'>
            <div class='flex items-center gap-2 max-w-[45%]'>
              \${safeImg(h.logo,'w-6 h-6 rounded-full','home')}
              <span class='truncate'>\${h.name||''}</span>
            </div>
            <span class='text-gray-500 text-sm'>vs</span>
            <div class='flex items-center gap-2 max-w-[45%] justify-end'>
              <span class='truncate text-right'>\${a.name||''}</span>
              \${safeImg(a.logo,'w-6 h-6 rounded-full','away')}
            </div>
          </div>\`;
      }

      // Mittlerer Block: Odds oder Ergebnis
      let middle='';
      if(type==='past' && res){
        middle = \`
          <div class="mt-2 grid grid-cols-3 text-center text-sm">
            <div>H<br><b>\${res.home}</b></div>
            <div>Erg.<br><b>:</b></div>
            <div>A<br><b>\${res.away}</b></div>
          </div>\`;
      } else {
        middle = \`
          <div class="mt-2 grid grid-cols-3 text-center text-sm">
            <div>1<br><b>\${odds?.home||'-'}</b></div>
            <div>X<br><b>\${odds?.draw||'-'}</b></div>
            <div>2<br><b>\${odds?.away||'-'}</b></div>
          </div>\`;
      }

      // Pick-Badge (neue Felder)
      let pickRow = '';
      if (it.pick && (type==='pick' || type==='upcoming' || (type==='live'))) {
        const map = {H:'1',D:'X',A:'2'};
        pickRow = \`
          <div class="mt-2 flex items-center justify-between text-xs">
            <span class="pickpill">Pick: \${map[it.pick.selection]} • Pred: \${Math.round(it.pick.predictionScore)}% • FormΔ: \${Math.round(it.pick.formScore)} • TableΔ: \${it.pick.tableScore}</span>
            <span class="text-gray-300">Score: \${it.pick.score}</span>
          </div>\`;
      }

      // Evaluations-Icon (nur past) + manuelle Buttons
      let evalRow = '';
      if(type==='past'){
        const ev = effectiveEval(it); // auto oder override
        const icon = ev==='correct'
          ? '<span class="eval-icon">✅</span>'
          : ev==='wrong'
          ? '<span class="eval-icon">❌</span>'
          : '';

        evalRow = \`
          <div class="mt-2 flex items-center justify-between">
            <div>\${icon}</div>
            <div class="flex items-center gap-2 text-xs">
              <button class="px-2 py-1 rounded bg-green-700/30 border border-green-700 hover:bg-green-700/40" title="Als richtig markieren" data-act="ok">✔ Richtig</button>
              <button class="px-2 py-1 rounded bg-rose-700/30  border border-rose-700  hover:bg-rose-700/40"  title="Als falsch markieren" data-act="bad">✖ Falsch</button>
            </div>
          </div>\`;
      }

      card.innerHTML=\`
        <div class='flex justify-between items-center text-xs text-gray-400 mb-2'>
          <div class='flex items-center gap-2'>
            \${safeImg(lg.logo,'w-4 h-4','league')}
            <button class="favbtn" title="Favorit">⭐</button>
            <span class="truncate">\${lg.name||''}</span>
          </div>
          <div class="flex items-center gap-2">
            \${liveLabel}
            <span>\${d?d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}):''}</span>
          </div>
        </div>
        \${teamRow}
        \${middle}
        \${pickRow}
        <div class="mt-2 text-xs text-cyan-400">\${it.prediction?.predictions?.advice || '-'}</div>
        \${evalRow}
      \`;

      card.addEventListener('click',(e)=>{ if(e.target.closest('.favbtn')||e.target.dataset.act) return; openDetails(it); });
      const favBtn = card.querySelector('.favbtn'); if(favBtn) favBtn.addEventListener('click',(e)=>{ e.stopPropagation(); toggleFav(fid); });
      if(type==='past'){
        const ok = card.querySelector('[data-act="ok"]');
        const bad = card.querySelector('[data-act="bad"]');
        ok.addEventListener('click',(e)=>{ e.stopPropagation(); setOverride(fid,'correct'); });
        bad.addEventListener('click',(e)=>{ e.stopPropagation(); setOverride(fid,'wrong'); });
      }

      return card;
    }

    function pct(x){ if(x==null) return 0; if(typeof x==='string' && x.endsWith('%')) return parseInt(x)||0; return +x||0; }
    function bar(label,left,right){
      const L=pct(left), R=pct(right);
      return \`
        <div class="mb-3">
          <div class="flex justify-between text-[12px] text-gray-300 mb-1">
            <span class="uppercase tracking-wide">\${label}</span>
            <span>\${L}% | \${R}%</span>
          </div>
          <div class="track">
            <div class="leftbar" style="width:\${L}%"></div>
            <div class="rightbar" style="width:\${R}%"></div>
          </div>
        </div>\`;
    }
    function openDetails(it){
      const p=it.prediction?.predictions||{};
      const c=it.prediction?.comparison||{};
      const h2h=(it.prediction?.h2h||[]).slice(0,3);
      const perc=p.percent||{};
      const blocks=[];
      blocks.push(bar("WINS THE GAME", perc.home, perc.away)); // ganz oben
      blocks.push(bar("STRENGTH", c.total?.home, c.total?.away));
      blocks.push(bar("ATTACKING POTENTIAL", c.att?.home, c.att?.away));
      blocks.push(bar("DEFENSIVE POTENTIAL", c.def?.home, c.def?.away));
      blocks.push(bar("POISSON DISTRIBUTION", c.poisson_distribution?.home, c.poisson_distribution?.away));
      blocks.push(bar("STRENGTH H2H", c.h2h?.home, c.h2h?.away));
      blocks.push(bar("GOALS H2H", c.goals?.home, c.goals?.away));

      detailsBody.innerHTML="";
      detailsBody.innerHTML+=\`<div><b>Empfehlung:</b> \${p.advice || '-'}</div>\`;
      detailsBody.innerHTML+=\`<div><b>Gewinner (Modell):</b> \${p.winner?.name || '-'}</div>\`;
      detailsBody.innerHTML+=\`<div><b>Prozente:</b> H:\${perc.home||'-'} • D:\${perc.draw||'-'} • A:\${perc.away||'-'}</div>\`;
      // Wenn es ein Top-Pick ist, zeige Begründung
      if(it.pick){
        const ex = it.pick.explain || {};
        detailsBody.innerHTML+=\`<div class="mt-2 text-xs text-gray-300"><b>Warum dieser Pick?</b>
          <div>Score: \${it.pick.score} • Pred: \${Math.round(it.pick.predictionScore)}% • FormΔ: \${Math.round(it.pick.formScore)} • TableΔ: \${it.pick.tableScore}</div>
        </div>\`;
      }
      detailsBody.innerHTML+='<hr class="border-[#1f2937] my-2"/>';
      detailsBody.innerHTML+=blocks.join('');
      if(h2h.length){
        detailsBody.innerHTML+='<hr class="border-[#1f2937] my-2"/><b>Letzte 3 Direktduelle:</b>';
        for(const m of h2h){
          detailsBody.innerHTML+=\`<div class="text-gray-400 text-xs">\${m.teams.home.name} \${m.goals.home}-\${m.goals.away} \${m.teams.away.name} (\${m.league.name}, \${new Date(m.fixture.date).toLocaleDateString()})</div>\`;
        }
      }
      dlg.showModal();
    }

    /* ---- Statistik „Vergangen“ ---- */
    function updatePastSummary(){
      if(!data){ pastSummary.textContent=''; return; }
      const past = data.sections.past || [];
      if(!past.length){ pastSummary.textContent = 'Keine vergangenen Spiele.'; return; }
      let correct=0, total=0;
      for(const it of past){
        const ev = effectiveEval(it);
        if(ev===null) continue; // nur ausgewertete
        total++;
        if(ev==='correct') correct++;
      }
      pastSummary.textContent = total? (\`Treffer heute: \${correct}/\${total} richtig\`) : 'Noch keine auswertbaren Spiele.';
    }

    // export
    window.toggleFav = toggleFav;
  </script>
</body>
</html>`);
});

/* ---------- Start ---------- */
app.listen(PORT, ()=> console.log("✔ Dashboard läuft auf http://localhost:"+PORT));
