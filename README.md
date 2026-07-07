# Autonomous Multi-Agent FinOps & Cloud Infrastructure Optimizer

An autonomous multi-agent system built using LangGraph, Pydantic guardrails, and CDKTF to monitor underutilized cloud resources, propose downsizing actions, and execute infrastructure resizes safely

## Architecture Overview

1. **Telemetry Generator:** Simulates cloud microservices emitting real-time metrics (CPU, memory, cost).
2. **Telemetry Ingestion:** Ingests metrics into a database (PostgreSQL/TimescaleDB or local SQLite fallback).
3. **FinOps Analyst Agent (LangGraph):** Periodically scans historical metrics, finding underutilized services (e.g., CPU < 15%).
4. **DevOps Agent (LangGraph):** Generates downsizing recommendations, creates rationales, and validates options.
5. **Safety Guardrails:** Enforces cooldowns per service (e.g., max 1 resize per 4 hours) and validates instance types against allow-lists.
6. **Infrastructure as Code (IaC):** Generates CDKTF stack files, verifies changes via simulated/real `cdktf plan`, and deploys.
7. **Streamlit Dashboard:** Shows live cost savings, fleet cost graphs, and agent audit trails.

---

## Local Run Instructions

This project works out-of-the-box with a zero-config local fallback (SQLite + Mock Kafka) if PostgreSQL and Redpanda are not running.

### 1. Installation
```bash
git clone https://github.com/karuna0733/Finops-optimizer.git
cd Finops-optimizer/finops-optimizer
python -m venv venv
source venv/bin/activate  # On Windows use: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Run Telemetry Simulation
Start the telemetry generator and consumer in separate terminal windows:

*   **Terminal A (Generator):**
    ```bash
    python telemetry/telemetry_generator.py
    ```
*   **Terminal B (Consumer):**
    ```bash
    python telemetry/telemetry_consumer.py
    ```

### 3. Run Agent Reasoning Cycle
Run the agent cycle manually to check for resizing opportunities and verify guardrails:
```bash
python agents/agent_graph.py
```

### 4. Start the Dashboard
Launch the Streamlit dashboard to monitor savings and run cycles interactively:
```bash
streamlit run dashboard/dashboard.py
```
View the dashboard at `http://localhost:8501`.
