-- === BetBot Schema (ORM-kompatibel) ===

-- Fixtures-Basisdaten (wie in db_models.Fixture)
CREATE TABLE IF NOT EXISTS fixtures (
  fixture_id      BIGINT PRIMARY KEY,
  league_id       BIGINT,
  league_name     TEXT,
  season          INT,
  home_id         BIGINT,
  home_name       TEXT,
  away_id         BIGINT,
  away_name       TEXT,
  created_at      TIMESTAMPTZ DEFAULT now(),
  updated_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_fixtures_league_season ON fixtures (league_id, season);

-- Snapshots (wie in db_models.Snapshot + live_monitor)
CREATE TABLE IF NOT EXISTS snapshots (
  id              BIGSERIAL PRIMARY KEY,
  ts_utc          TIMESTAMPTZ NOT NULL DEFAULT timezone('UTC', now()),
  fixture_id      BIGINT NOT NULL REFERENCES fixtures(fixture_id) ON DELETE CASCADE,
  minute          INT DEFAULT 0,

  home_sog        INT DEFAULT 0,
  home_shots      INT DEFAULT 0,
  home_corners    INT DEFAULT 0,
  home_saves      INT DEFAULT 0,
  home_poss       REAL DEFAULT 0.0,

  away_sog        INT DEFAULT 0,
  away_shots      INT DEFAULT 0,
  away_corners    INT DEFAULT 0,
  away_saves      INT DEFAULT 0,
  away_poss       REAL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS ix_snap_fixture_ts ON snapshots (fixture_id, ts_utc);

-- Live-Odds (kompakte Variante, wie in db_models.OddsLive & live_monitor)
CREATE TABLE IF NOT EXISTS odds_live (
  id              BIGSERIAL PRIMARY KEY,
  ts_utc          TIMESTAMPTZ NOT NULL DEFAULT timezone('UTC', now()),
  fixture_id      BIGINT NOT NULL REFERENCES fixtures(fixture_id) ON DELETE CASCADE,

  goalline        REAL,
  over_odds       REAL,
  under_odds      REAL,

  home_ml         REAL,
  draw_ml         REAL,
  away_ml         REAL
);
CREATE INDEX IF NOT EXISTS ix_odds_fixture_ts ON odds_live (fixture_id, ts_utc);

-- Alerts (wie in db_models.Alert & live_monitor)
CREATE TABLE IF NOT EXISTS alerts (
  id              BIGSERIAL PRIMARY KEY,
  ts_utc          TIMESTAMPTZ NOT NULL DEFAULT timezone('UTC', now()),
  fixture_id      BIGINT NOT NULL REFERENCES fixtures(fixture_id) ON DELETE CASCADE,
  kind            TEXT NOT NULL,     -- z.B. "GOAL_SOON" | "WIN_TREND"
  message         TEXT NOT NULL,
  details         TEXT               -- JSON als Text (kompatibel zu db_models.Alert)
);
CREATE INDEX IF NOT EXISTS ix_alerts_fixture_ts ON alerts (fixture_id, ts_utc DESC);
