-- Telemetry hypertable for cloud infrastructure metrics
CREATE TABLE IF NOT EXISTS cloud_telemetry (
    time            TIMESTAMPTZ       NOT NULL,
    service_id      TEXT              NOT NULL,
    cpu_utilization_pct    DOUBLE PRECISION,
    memory_utilization_pct DOUBLE PRECISION,
    request_count           INTEGER,
    network_egress_mb       DOUBLE PRECISION,
    current_instance_type   TEXT,
    hourly_cost_usd          DOUBLE PRECISION
);

SELECT create_hypertable('cloud_telemetry', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_service_time ON cloud_telemetry (service_id, time DESC);

-- Audit trail of every agent action (decisions, code written, cooldowns, savings)
CREATE TABLE IF NOT EXISTS agent_audit_log (
    id              SERIAL PRIMARY KEY,
    time            TIMESTAMPTZ NOT NULL DEFAULT now(),
    service_id      TEXT NOT NULL,
    agent_name      TEXT NOT NULL,
    action          TEXT NOT NULL,          -- e.g. 'ANALYSIS', 'PLAN', 'APPLY', 'REJECTED'
    details         JSONB,
    estimated_monthly_savings_usd DOUBLE PRECISION DEFAULT 0
);

-- Tracks last-modified timestamp per service to enforce cooldown windows
CREATE TABLE IF NOT EXISTS resize_cooldown (
    service_id      TEXT PRIMARY KEY,
    last_modified   TIMESTAMPTZ NOT NULL
);
