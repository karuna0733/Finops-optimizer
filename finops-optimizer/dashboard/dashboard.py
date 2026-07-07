"""
dashboard.py

Phase 4.3 -- Streamlit dashboard showing:
  - Live cost/CPU metrics per service (auto-refreshing)
  - Total fleet hourly/monthly cost trend
  - Agent audit trail (what was analyzed, proposed, rejected, applied)
  - A button to trigger one agent reasoning cycle on demand

Run with: streamlit run dashboard/dashboard.py
"""

import os
import sys
import time

import pandas as pd
import streamlit as st

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "db"))
import db_helper

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "agents"))

DB_CONFIG = dict(
    host=os.environ.get("DB_HOST", "localhost"),
    port=os.environ.get("DB_PORT", "5432"),
    dbname=os.environ.get("DB_NAME", "finops_telemetry"),
    user=os.environ.get("DB_USER", "finops"),
    password=os.environ.get("DB_PASSWORD", "finops"),
)

st.set_page_config(page_title="FinOps Multi-Agent Optimizer", layout="wide")


@st.cache_resource
def get_db_info():
    conn, db_type = db_helper.get_db_connection(DB_CONFIG)
    return {"conn": conn, "type": db_type}


def query_df(sql, params=None):
    info = get_db_info()
    conn = info["conn"]
    db_type = info["type"]
    translated = db_helper.translate_sql(sql, db_type)
    df = pd.read_sql(translated, conn, params=params)
    if 'time' in df.columns:
        df['time'] = pd.to_datetime(df['time'])
    return df


st.title("🤖 Autonomous Multi-Agent FinOps Optimizer")
st.caption("Live infrastructure telemetry, agent reasoning, and IaC execution audit trail")

col1, col2, col3, col4 = st.columns(4)

try:
    fleet_cost = query_df(
        """SELECT sum(hourly_cost_usd) AS total FROM (
               SELECT hourly_cost_usd FROM cloud_telemetry
               WHERE (service_id, time) IN (
                   SELECT service_id, max(time) FROM cloud_telemetry GROUP BY service_id
               )
           ) t"""
    )
    total_hourly = fleet_cost["total"].iloc[0] or 0
    col1.metric("Fleet Hourly Cost", f"${total_hourly:,.2f}")
    col2.metric("Projected Monthly Cost", f"${total_hourly * 730:,.2f}")

    savings = query_df(
        "SELECT coalesce(sum(estimated_monthly_savings_usd),0) AS s "
        "FROM agent_audit_log WHERE action = 'APPLY'"
    )
    col3.metric("Monthly Savings Achieved", f"${savings['s'].iloc[0]:,.2f}")

    applied = query_df("SELECT count(*) AS c FROM agent_audit_log WHERE action = 'APPLY'")
    col4.metric("Resizes Applied", int(applied["c"].iloc[0]))
except Exception as e:
    st.warning(f"Waiting for data... ({e})")

st.divider()

if st.button("▶ Run Agent Reasoning Cycle Now", type="primary"):
    with st.spinner("FinOps Analyst is reviewing fleet metrics, DevOps Agent is drafting IaC..."):
        from agent_graph import run_once
        result = run_once()
    st.success("Cycle complete -- see audit trail below.")
    st.json(result["audit_log"])

left, right = st.columns([2, 1])

with left:
    st.subheader("📊 Live Service Metrics (last 2 hours)")
    try:
        metrics = query_df(
            """
            SELECT time, service_id, cpu_utilization_pct, memory_utilization_pct,
                   current_instance_type, hourly_cost_usd
            FROM cloud_telemetry
            WHERE time > now() - interval '2 hours'
            ORDER BY time DESC
            LIMIT 500
            """
        )
        if not metrics.empty:
            pivot = metrics.pivot_table(
                index="time", columns="service_id", values="cpu_utilization_pct", aggfunc="mean"
            )
            st.line_chart(pivot.iloc[:, :8])  # show first 8 services to keep it readable
            st.dataframe(
                metrics.sort_values("time", ascending=False).head(50),
                use_container_width=True,
            )
        else:
            st.info("No telemetry yet -- start telemetry_generator.py and telemetry_consumer.py")
    except Exception as e:
        st.error(f"Could not load telemetry: {e}")

with right:
    st.subheader("📋 Agent Audit Trail")
    try:
        audit = query_df(
            "SELECT time, agent_name, action, service_id, details, "
            "estimated_monthly_savings_usd FROM agent_audit_log "
            "ORDER BY time DESC LIMIT 30"
        )
        for _, row in audit.iterrows():
            icon = {"ANALYSIS": "🔍", "PLAN": "📝", "APPLY": "✅",
                    "REJECTED": "🚫", "PLAN_FAILED": "⚠️"}.get(row["action"], "•")
            st.markdown(f"{icon} **{row['action']}** — `{row['agent_name']}` "
                        f"({row['time'].strftime('%H:%M:%S')})")
            import json
            details_val = row["details"]
            if isinstance(details_val, str):
                try:
                    details_val = json.loads(details_val)
                except Exception:
                    pass
            st.caption(str(details_val))
            if row["estimated_monthly_savings_usd"]:
                st.caption(f"💰 Est. savings: ${row['estimated_monthly_savings_usd']:.2f}/mo")
            st.divider()
    except Exception as e:
        st.warning(f"No audit entries yet. ({e})")

st.caption("Auto-refresh every 10s")
time.sleep(10)
st.rerun()
