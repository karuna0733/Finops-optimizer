"""
agent_graph.py

The Multi-Agent Brain (Phase 2). Two LangGraph nodes:

1. FinOpsAnalystAgent  -- queries TimescaleDB for average CPU/mem over the
   last N hours per service, flags chronically underutilized services,
   and proposes a smaller instance type using a local Ollama model.

2. DevOpsAgent -- takes the Analyst's structured finding, asks Ollama to
   draft a one-line rationale + confirm the target instance type, then
   hands the proposal to the guardrails layer (guardrails.py) before
   anything touches the CDKTF sandbox (sandbox/cdktf_runner.py).

If `cdktf plan` (simulated) fails, the error is routed back into the
DevOpsAgent node, which retries up to MAX_RETRIES times -- this is the
"verifies, and loops back if incorrect" cyclical behavior LangGraph enables.

Run with: python agents/agent_graph.py
Requires: `ollama pull llama3:8b` (or change OLLAMA_MODEL below)
"""

import json
import os
import sys
from typing import TypedDict, Optional

import requests
from langgraph.graph import StateGraph, END

sys.path.append(os.path.dirname(__file__))
from guardrails import enforce_guardrails, GuardrailRejection, ALLOWED_INSTANCE_TYPES

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "sandbox"))
from cdktf_runner import write_resize_config, run_cdktf_plan, apply_resize

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "db"))
import db_helper

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3:8b")
MAX_RETRIES = 3

DB_CONFIG = dict(
    host=os.environ.get("DB_HOST", "localhost"),
    port=os.environ.get("DB_PORT", "5432"),
    dbname=os.environ.get("DB_NAME", "finops_telemetry"),
    user=os.environ.get("DB_USER", "finops"),
    password=os.environ.get("DB_PASSWORD", "finops"),
)

INSTANCE_DOWNGRADE_PATH = ["t3.xlarge", "t3.large", "t3.medium", "t3.small", "t3.micro"]


def call_ollama(prompt: str) -> str:
    """Minimal wrapper around local Ollama inference. Falls back to a
    deterministic rule-based response if Ollama isn't running, so the
    pipeline still works end-to-end without GPU setup."""
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        print(f"[ollama] unreachable ({e}), using rule-based fallback")
        return ""


class AgentState(TypedDict):
    underutilized_services: list
    current_target: Optional[dict]
    proposal: Optional[dict]
    plan_result: Optional[dict]
    retries: int
    audit_log: list


def log_action(state: AgentState, agent_name: str, action: str, details: dict, savings: float = 0.0):
    entry = {"agent_name": agent_name, "action": action, "details": details,
              "estimated_monthly_savings_usd": savings}
    state["audit_log"].append(entry)

    conn, db_type = db_helper.get_db_connection(DB_CONFIG)
    cur = conn.cursor()
    sql = """INSERT INTO agent_audit_log
           (service_id, agent_name, action, details, estimated_monthly_savings_usd)
           VALUES (%s, %s, %s, %s, %s)"""
    sql = db_helper.translate_sql(sql, db_type)
    cur.execute(
        sql,
        (details.get("service_id", "N/A"), agent_name, action, json.dumps(details), savings),
    )
    conn.commit()
    cur.close()
    conn.close()


# ---------------------------------------------------------------------------
# NODE 1: FinOps Analyst Agent
# ---------------------------------------------------------------------------
def finops_analyst_node(state: AgentState) -> AgentState:
    conn, db_type = db_helper.get_db_connection(DB_CONFIG)
    cur = conn.cursor()
    sql = """
        SELECT service_id,
               avg(cpu_utilization_pct) AS avg_cpu,
               avg(memory_utilization_pct) AS avg_mem,
               max(current_instance_type) AS instance_type,
               max(hourly_cost_usd) AS hourly_cost
        FROM cloud_telemetry
        WHERE time > now() - interval '2 hours'
        GROUP BY service_id
        HAVING avg(cpu_utilization_pct) < 15
        ORDER BY avg_cpu ASC
        LIMIT 10
        """
    sql = db_helper.translate_sql(sql, db_type)
    cur.execute(sql)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    underutilized = [
        {
            "service_id": r[0],
            "avg_cpu": round(r[1], 2),
            "avg_mem": round(r[2], 2),
            "current_instance_type": r[3],
            "hourly_cost_usd": r[4],
        }
        for r in rows
    ]

    print(f"[FinOpsAnalyst] found {len(underutilized)} underutilized services")
    state["underutilized_services"] = underutilized

    if underutilized:
        target = underutilized[0]
        state["current_target"] = target
        log_action(
            state, "FinOpsAnalyst", "ANALYSIS",
            {"service_id": target["service_id"],
             "avg_cpu": target["avg_cpu"],
             "msg": f"{target['service_id']} running at {target['avg_cpu']}% CPU "
                    f"on {target['current_instance_type']} -- wasting money."}
        )
    else:
        state["current_target"] = None

    return state


def propose_downgrade(current_type: str) -> Optional[str]:
    if current_type not in INSTANCE_DOWNGRADE_PATH:
        return None
    idx = INSTANCE_DOWNGRADE_PATH.index(current_type)
    if idx + 1 < len(INSTANCE_DOWNGRADE_PATH):
        return INSTANCE_DOWNGRADE_PATH[idx + 1]
    return None


# ---------------------------------------------------------------------------
# NODE 2: DevOps Agent
# ---------------------------------------------------------------------------
def devops_agent_node(state: AgentState) -> AgentState:
    target = state["current_target"]
    if target is None:
        return state

    proposed_type = propose_downgrade(target["current_instance_type"])
    if proposed_type is None:
        log_action(state, "DevOpsAgent", "REJECTED",
                   {"service_id": target["service_id"], "msg": "Already at smallest tier."})
        state["current_target"] = None
        return state

    # Ask the LLM to articulate a rationale (or fall back to a template)
    prompt = (
        f"Service {target['service_id']} is running at {target['avg_cpu']}% CPU and "
        f"{target['avg_mem']}% memory on a {target['current_instance_type']} instance. "
        f"In one short sentence, justify downsizing it to {proposed_type}."
    )
    rationale = call_ollama(prompt) or (
        f"Service is consistently underutilized ({target['avg_cpu']}% CPU); "
        f"downsizing from {target['current_instance_type']} to {proposed_type} is safe and cost-saving."
    )

    cost_table = {"t3.micro": 0.0104, "t3.small": 0.0208, "t3.medium": 0.0416,
                  "t3.large": 0.0832, "t3.xlarge": 0.1664}
    monthly_savings = round(
        (cost_table[target["current_instance_type"]] - cost_table[proposed_type]) * 730, 2
    )

    raw_proposal = {
        "service_id": target["service_id"],
        "current_instance_type": target["current_instance_type"],
        "proposed_instance_type": proposed_type,
        "reason": rationale,
        "estimated_monthly_savings_usd": monthly_savings,
    }

    print(f"[DevOpsAgent] proposing: {raw_proposal}")

    # --- GUARDRAIL CHECKPOINT ---
    try:
        validated = enforce_guardrails(raw_proposal, DB_CONFIG)
    except GuardrailRejection as e:
        print(f"[GUARDRAIL] rejected: {e.reason}")
        log_action(state, "DevOpsAgent", "REJECTED",
                   {"service_id": target["service_id"], "reason": e.reason})
        state["current_target"] = None
        state["proposal"] = None
        return state

    state["proposal"] = validated.model_dump()
    log_action(state, "DevOpsAgent", "PLAN", state["proposal"], monthly_savings)
    return state


# ---------------------------------------------------------------------------
# NODE 3: CDKTF Plan + Apply (the execution sandbox)
# ---------------------------------------------------------------------------
def cdktf_execution_node(state: AgentState) -> AgentState:
    proposal = state.get("proposal")
    if proposal is None:
        return state

    write_resize_config(proposal["service_id"], proposal["proposed_instance_type"])
    plan_result = run_cdktf_plan()
    state["plan_result"] = plan_result

    if plan_result["success"]:
        apply_resize(proposal["service_id"], proposal["proposed_instance_type"], DB_CONFIG)
        log_action(state, "DevOpsAgent", "APPLY", proposal,
                   proposal["estimated_monthly_savings_usd"])
        print(f"[CDKTF] plan passed & applied: {proposal['service_id']} -> "
              f"{proposal['proposed_instance_type']} "
              f"(saving ~${proposal['estimated_monthly_savings_usd']}/mo)")
    else:
        state["retries"] += 1
        log_action(state, "DevOpsAgent", "PLAN_FAILED",
                   {"service_id": proposal["service_id"], "error": plan_result["error"]})
        print(f"[CDKTF] plan FAILED (retry {state['retries']}/{MAX_RETRIES}): "
              f"{plan_result['error']}")

    return state


def should_retry(state: AgentState) -> str:
    plan_result = state.get("plan_result")
    if plan_result is None or plan_result.get("success"):
        return "done"
    if state["retries"] >= MAX_RETRIES:
        print("[CDKTF] max retries hit, giving up on this service.")
        return "done"
    return "retry"


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("finops_analyst", finops_analyst_node)
    graph.add_node("devops_agent", devops_agent_node)
    graph.add_node("cdktf_execution", cdktf_execution_node)

    graph.set_entry_point("finops_analyst")
    graph.add_edge("finops_analyst", "devops_agent")
    graph.add_edge("devops_agent", "cdktf_execution")
    graph.add_conditional_edges(
        "cdktf_execution",
        should_retry,
        {"retry": "devops_agent", "done": END},
    )
    return graph.compile()


def run_once():
    app = build_graph()
    initial_state: AgentState = {
        "underutilized_services": [],
        "current_target": None,
        "proposal": None,
        "plan_result": None,
        "retries": 0,
        "audit_log": [],
    }
    final_state = app.invoke(initial_state)
    print("\n=== AUDIT LOG ===")
    for entry in final_state["audit_log"]:
        print(entry)
    return final_state


if __name__ == "__main__":
    run_once()
