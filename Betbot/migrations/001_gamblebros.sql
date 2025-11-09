-- Stammdaten (Logos/Namen cachen)
CREATE TABLE IF NOT EXISTS league_meta (
  league_id    BIGINT PRIMARY KEY,
  name         TEXT NOT NULL,
  country      TEXT,
  logo_url     TEXT,
  updated_at   TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS team_meta (
  team_id      BIGINT PRIMARY KEY,
  name         TEXT NOT NULL,
  country      TEXT,
  logo_url     TEXT,
  updated_at   TIMESTAMPTZ DEFAULT now()
);

-- Provider-Predictions (roh, 1×/Tag je Spiel)
CREATE TABLE IF NOT EXISTS provider_predictions (
  fixture_id   BIGINT,
  provider     TEXT NOT NULL,   -- 'api-football'
  payload      JSONB NOT NULL,
  fetched_at   TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (fixture_id, provider, fetched_at)
);

-- Geplante/aktualisierte Pre-Match-Tipps (für Heute/Morgen/Übermorgen)
CREATE TABLE IF NOT EXISTS gb_prematch_candidates (
  id           BIGSERIAL PRIMARY KEY,
  fixture_id   BIGINT NOT NULL,
  day_bucket   TEXT NOT NULL,    -- 'TODAY' | 'TOMORROW' | 'OVERMORROW'
  kickoff_utc  TIMESTAMPTZ NOT NULL,

  league_id    BIGINT,
  home_team_id BIGINT,
  away_team_id BIGINT,

  market       TEXT NOT NULL,    -- z.B. 'FT_1X2','DC_1X','DNB_HOME','OU_2_5_OVER','BTTS_Y'
  selection    TEXT NOT NULL,
  line         TEXT,             -- z.B. '2.5' für OU, '0.25' für AH

  best_price   NUMERIC(8,3),
  q_implied    NUMERIC(6,4),     -- aus best_price (overround-bereinigt optional)
  p_est        NUMERIC(6,4),     -- Ensemble-Wahrscheinlichkeit
  edge_pp      NUMERIC(6,4),
  confidence   NUMERIC(5,2),     -- 0..1 oder 0..100 (einheitlich halten)

  source_flags TEXT[],           -- ['MARKET','MODEL','PREDICTIONS']
  status       TEXT NOT NULL DEFAULT 'SCHEDULED',  -- SCHEDULED/ACTIVE/REVIEW/UPGRADE

  created_at   TIMESTAMPTZ DEFAULT now(),
  updated_at   TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_gb_prematch ON gb_prematch_candidates(fixture_id, market, selection);

-- Veröffentlicht/gefroren (stabile, sichtbare Tipps für die Seite)
CREATE TABLE IF NOT EXISTS gb_tip_events (
  id              BIGSERIAL PRIMARY KEY,
  fixture_id      BIGINT NOT NULL,
  kickoff_utc     TIMESTAMPTZ NOT NULL,

  league_id       BIGINT,
  home_team_id    BIGINT,
  away_team_id    BIGINT,

  market          TEXT NOT NULL,
  selection       TEXT NOT NULL,
  line            TEXT,

  published_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  status          TEXT NOT NULL DEFAULT 'OPEN',   -- OPEN/HIT/MISS/VOID

  best_price      NUMERIC(8,3),
  q_implied       NUMERIC(6,4),
  p_est           NUMERIC(6,4),
  edge_pp         NUMERIC(6,4),
  confidence      NUMERIC(5,2),

  reason_code     TEXT,        -- z.B. 'VALUE_PREMATCH'
  rationale       TEXT,

  source_candidate BIGINT REFERENCES gb_prematch_candidates(id),
  settled_at      TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_gb_events ON gb_tip_events(fixture_id, market, selection);

-- Performanz
CREATE INDEX IF NOT EXISTS ix_gb_cands_bucket ON gb_prematch_candidates(day_bucket, kickoff_utc);
CREATE INDEX IF NOT EXISTS ix_gb_events_time  ON gb_tip_events(kickoff_utc);
