# Autonomous Multi-Agent FinOps & Cloud Infrastructure Optimizer

A working local implementation: a Python telemetry generator simulates 50 microservices,
streams metrics through Redpanda (Kafka-compatible) into TimescaleDB, a LangGraph
two-agent workflow (FinOps Analyst -> DevOps Agent) reasons over that data using a
local Ollama model, proposes infrastructure resizes, runs those proposals through a
strict Pydantic guardrail + cooldown layer, generates a CDKTF stack file, simulates
`cdktf plan`, applies the change, and a Streamlit dashboard shows it all live.

## 0. Prerequisites

- Docker + Docker Compose
- Python 3.10+
- (Optional, for real local LLM inference) [Ollama](https://ollama.com)

## 1. Install Python dependencies

```bash
cd finops-optimizer
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Start infrastructure (Redpanda + TimescaleDB)

```bash
docker compose up -d
```

Wait ~15s for healthchecks to pass. Verify:

```bash
docker ps                     # should show redpanda, redpanda-console, timescaledb all healthy
```

The schema in `db/init.sql` is auto-applied on first boot (creates `cloud_telemetry`,
`agent_audit_log`, `resize_cooldown`). You can browse the Kafka topic visually at
http://localhost:8081 (Redpanda Console).

If you ever need to re-apply the schema manually:

```bash
docker exec -i timescaledb psql -U finops -d finops_telemetry < db/init.sql
```

## 3. (Optional but recommended) Set up local LLM via Ollama

```bash
ollama pull llama3:8b
ollama serve        # if not already running as a service
```

> If you skip this step, `agent_graph.py` automatically falls back to deterministic
> rule-based rationale text — the pipeline still runs end-to-end, you just lose the
> natural-language reasoning from the LLM.

## 4. Start the telemetry stream (two terminals)

**Terminal A — producer (simulates 50 services):**
```bash
source venv/bin/activate
python telemetry/telemetry_generator.py
```

**Terminal B — consumer (writes into TimescaleDB):**
```bash
source venv/bin/activate
python telemetry/telemetry_consumer.py
```

Let this run for a minute or two so TimescaleDB accumulates enough history for the
Analyst Agent's 2-hour rolling average query to have data (it'll still work on a much
shorter window for a demo — query the table directly to check):

```bash
docker exec -it timescaledb psql -U finops -d finops_telemetry \
  -c "SELECT count(*) FROM cloud_telemetry;"
```

## 5. Run the multi-agent reasoning cycle

**Terminal C:**
```bash
source venv/bin/activate
python agents/agent_graph.py
```

This runs one full LangGraph cycle:
1. `finops_analyst_node` queries TimescaleDB for services averaging <15% CPU
2. `devops_agent_node` proposes a downgraded instance type, gets an LLM rationale,
   computes savings, and **passes it through `guardrails.py`** (Pydantic allow-list +
   cooldown check)
3. `cdktf_execution_node` writes a CDKTF stack file, runs a simulated `cdktf plan`,
   and if it passes, applies the change and starts the cooldown clock
4. If the plan fails, state loops back to `devops_agent_node` (up to 3 retries) —
   this is the self-correcting cyclical workflow LangGraph enables

You'll see console output for each step plus a final audit log dump. Everything is
also persisted to the `agent_audit_log` table.

## 6. Launch the dashboard

**Terminal D:**
```bash
source venv/bin/activate
streamlit run dashboard/dashboard.py
```

Open http://localhost:8501. You'll see:
- Live fleet cost metrics, auto-refreshing every 10s
- A "Run Agent Reasoning Cycle Now" button to trigger Step 5 on demand from the UI
- Per-service CPU line chart
- Full agent audit trail (analysis -> plan -> apply/reject) with savings estimates

## 7. Watch the closed feedback loop

Once `agent_graph.py` applies a resize, it writes to `sandbox/applied_resizes.json`.
The running `telemetry_generator.py` polls this file every tick (5s) and immediately
starts emitting metrics consistent with the new, smaller instance type (higher CPU%
for the same workload, lower `hourly_cost_usd`). Watch the dashboard's CPU chart and
cost metric shift after a resize — that's Phase 4 in action.

---

## How the safety guardrails work (`agents/guardrails.py`)

- **Hallucination guardrail**: every proposal from the DevOps Agent is parsed into a
  strict `pydantic.BaseModel` (`ResizeProposal`). Both `current_instance_type` and
  `proposed_instance_type` are validated against a hardcoded allow-list
  (`ALLOWED_INSTANCE_TYPES`). If the LLM invents a nonexistent type like
  `t3.ultra-mega`, validation raises and the action is rejected before any code is
  generated — never trust the model's raw string output for anything destructive.
- **Cooldown guardrail**: `resize_cooldown` table tracks `last_modified` per service.
  Before any new resize, `check_cooldown()` blocks the action if under 4 hours have
  passed since the last change to that service, preventing flapping/thrashing on
  noisy traffic spikes.
- **Plan-before-apply**: nothing is "applied" without first passing a `cdktf plan`
  equivalent — real Terraform's plan step if `CDKTF_REAL=1` and the CLI is installed,
  or a deterministic simulated check otherwise.

## Going from simulation to real CDKTF/Terraform

The whole IaC sandbox is built to swap cleanly into the real thing:

1. `npm install -g cdktf-cli` and `pip install cdktf cdktf-cdktf-provider-aws constructs`
2. `cd sandbox/cdktf_app && cdktf init --template=python --local`
3. Replace the mock resource attributes in `STACK_TEMPLATE` (inside
   `sandbox/cdktf_runner.py`) with a real `Instance()` construct from
   `cdktf_cdktf_provider_aws.instance`, pointing at real AWS credentials.
4. Set `CDKTF_REAL=1` in your environment before running `agent_graph.py` —
   `run_cdktf_plan()` will then shell out to the actual `cdktf plan` CLI instead of
   the simulated check, and `cdktf apply --auto-approve` can be wired in the same way
   inside `apply_resize()`.

**Do this only against a sandbox/dev AWS account with tightly scoped IAM permissions**
(resize-only, specific instance tags) — never give the agent's execution role broad
production credentials.

## Project structure

```
finops-optimizer/
├── docker-compose.yml          # Redpanda + TimescaleDB
├── db/init.sql                 # Schema: telemetry, audit log, cooldown table
├── telemetry/
│   ├── telemetry_generator.py  # Simulates 50 services -> Kafka topic
│   └── telemetry_consumer.py   # Kafka topic -> TimescaleDB
├── agents/
│   ├── agent_graph.py          # LangGraph: FinOps Analyst + DevOps Agent
│   └── guardrails.py           # Pydantic allow-list + cooldown enforcement
├── sandbox/
│   ├── cdktf_runner.py         # Writes CDKTF stack, simulates plan/apply
│   ├── cdktf_app/main.py       # Generated IaC stack (auto-written)
│   └── applied_resizes.json    # Shared state the telemetry gen reacts to
├── dashboard/
│   └── dashboard.py            # Streamlit live view + audit trail
└── requirements.txt
```

## Common issues

- **"connection refused" to Postgres/Kafka**: Docker containers not fully healthy yet
  — wait longer or check `docker compose logs`.
- **Analyst finds 0 underutilized services**: let the producer/consumer run longer, or
  lower the `interval '2 hours'` / `< 15` thresholds in `agent_graph.py` for faster demo.
- **Ollama timeouts**: increase the `timeout=15` in `call_ollama()`, or just let the
  rule-based fallback handle it — the pipeline doesn't require Ollama to function.
