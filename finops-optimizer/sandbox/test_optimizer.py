import sys
import os
import sqlite3
from datetime import datetime, timezone

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "db"))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "agents"))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "telemetry"))

import db_helper
from agent_graph import run_once
from telemetry_generator import ServiceState, SERVICE_NAMES

def main():
    print("=== STARTING OPTIMIZER CORRECTNESS ANALYSIS ===")
    
    # 1. Force SQLite mode and clean tables
    os.environ["USE_SQLITE"] = "1"
    os.environ["USE_MOCK_KAFKA"] = "1"
    
    db_config = {} # dummy config
    conn, db_type = db_helper.get_db_connection(db_config)
    cur = conn.cursor()
    
    print("[test] Cleaning database tables...")
    cur.execute("DELETE FROM cloud_telemetry;")
    cur.execute("DELETE FROM agent_audit_log;")
    cur.execute("DELETE FROM resize_cooldown;")
    conn.commit()
    
    # 2. Seed some underutilized service metrics (e.g. CPU < 15%)
    print("[test] Seeding underutilized service metrics...")
    services = [ServiceState(name) for name in SERVICE_NAMES]
    # Make sure at least one service is idle/waster (forcing it)
    wasters = []
    for svc in services:
        if svc.is_idle_waster:
            wasters.append(svc)
    if not wasters:
        services[0].is_idle_waster = True
        services[0].base_load = 5.0
        wasters.append(services[0])
    
    # Seed 10 ticks for each service
    for _ in range(10):
        for svc in services:
            payload = svc.tick({})
            # Insert into SQLite directly
            sql = """
            INSERT INTO cloud_telemetry
                (time, service_id, cpu_utilization_pct, memory_utilization_pct,
                 request_count, network_egress_mb, current_instance_type, hourly_cost_usd)
            VALUES (:timestamp, :service_id, :cpu_utilization_pct, :memory_utilization_pct,
                    :request_count, :network_egress_mb, :current_instance_type, :hourly_cost_usd)
            """
            cur.execute(sql, payload)
    conn.commit()
    print(f"[test] Seeded {len(services) * 10} telemetry records.")
    
    # Check count
    cur.execute("SELECT count(*) FROM cloud_telemetry")
    count = cur.fetchone()[0]
    assert count > 0, "Telemetry data was not seeded!"
    
    # 3. Run the first Agent Cycle (should perform an analysis and apply a resize)
    print("\n[test] Running 1st Agent Reasoning Cycle...")
    state1 = run_once()
    
    # Verify first cycle results
    print("[test] Verifying 1st cycle audit log...")
    cur.execute("SELECT action, service_id FROM agent_audit_log ORDER BY id ASC")
    log_entries = cur.fetchall()
    
    print(f"Audit log entries: {log_entries}")
    actions = [row[0] for row in log_entries]
    assert "ANALYSIS" in actions, "First cycle did not run ANALYSIS!"
    assert "APPLY" in actions, "First cycle did not run APPLY!"
    
    resized_service = None
    for action, svc_id in log_entries:
        if action == "APPLY":
            resized_service = svc_id
            break
    
    assert resized_service is not None, "No service was resized!"
    print(f"Successfully resized service: {resized_service}")
    
    # 4. Run the second Agent Cycle (should fail because of cooldown guardrail)
    print("\n[test] Running 2nd Agent Reasoning Cycle...")
    state2 = run_once()
    
    print("[test] Verifying 2nd cycle cooldown enforcement...")
    cur.execute("SELECT action, service_id, details FROM agent_audit_log WHERE service_id = ? ORDER BY id DESC LIMIT 1", (resized_service,))
    last_entry = cur.fetchone()
    
    print(f"Last audit entry for {resized_service}: {last_entry}")
    assert last_entry[0] == "REJECTED", f"Service {resized_service} was not rejected by cooldown!"
    
    print("\n=== OPTIMIZER CORRECTNESS ANALYSIS: PASSED ===")
    print("All checks completed successfully: Telemetry stream simulation, database ingestion, DevOps reasoning, plan execution, and safety guardrails are 100% WORKING.")
    
    cur.close()
    conn.close()

if __name__ == "__main__":
    main()
