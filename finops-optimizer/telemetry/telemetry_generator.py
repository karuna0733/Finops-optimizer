"""
telemetry_generator.py

Simulates 50 microservices emitting CPU/Memory/cost telemetry continuously.
Publishes JSON payloads to a Redpanda/Kafka topic ('cloud-telemetry').

It also watches a local JSON file (sandbox/applied_resizes.json) that the
DevOps Agent writes to whenever it "applies" an infrastructure change. Once a
service is resized there, this generator immediately reflects the new
instance type's cost/capacity profile in the simulated metrics -- this is
what creates the closed feedback loop (Phase 4).
"""

import json
import os
import random
import time
from datetime import datetime, timezone

try:
    if os.environ.get("USE_MOCK_KAFKA", "1") == "1":
        raise ImportError()
    from kafka import KafkaProducer
except ImportError:
    import sys
    sys.path.append(os.path.dirname(__file__))
    from kafka_mock import MockKafkaProducer as KafkaProducer

KAFKA_BROKER = os.environ.get("KAFKA_BROKER", "localhost:9092")
TOPIC = "cloud-telemetry"
NUM_SERVICES = 50
APPLIED_RESIZES_PATH = os.path.join(
    os.path.dirname(__file__), "..", "sandbox", "applied_resizes.json"
)

# Instance type -> (hourly cost USD, relative capacity multiplier)
# Capacity multiplier affects how "busy" the CPU/mem % look for the same load.
INSTANCE_PROFILES = {
    "t3.micro":  {"hourly_cost_usd": 0.0104, "capacity": 1.0},
    "t3.small":  {"hourly_cost_usd": 0.0208, "capacity": 2.0},
    "t3.medium": {"hourly_cost_usd": 0.0416, "capacity": 4.0},
    "t3.large":  {"hourly_cost_usd": 0.0832, "capacity": 8.0},
    "t3.xlarge": {"hourly_cost_usd": 0.1664, "capacity": 16.0},
}

SERVICE_NAMES = [
    f"{name}-service-pod-{str(i).zfill(2)}"
    for i in range(1, NUM_SERVICES + 1)
    for name in [random.choice(
        ["auth", "billing", "catalog", "search", "checkout",
         "notification", "analytics", "gateway", "user", "inventory"]
    )]
][:NUM_SERVICES]


def load_applied_resizes():
    """Read any instance-type overrides the DevOps Agent has applied."""
    if os.path.exists(APPLIED_RESIZES_PATH):
        try:
            with open(APPLIED_RESIZES_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


class ServiceState:
    """Keeps a base 'real workload' per service so metrics look realistic
    (i.e. don't randomly teleport every tick) and so we can simulate
    chronically idle services that the FinOps agent should catch."""

    def __init__(self, service_id):
        self.service_id = service_id
        self.current_instance_type = "t3.medium"
        # ~30% of services are deliberately over-provisioned / idle
        self.is_idle_waster = random.random() < 0.3
        self.base_load = random.uniform(2, 15) if self.is_idle_waster else random.uniform(35, 75)

    def tick(self, applied_overrides):
        # Apply any resize the DevOps agent has committed
        if self.service_id in applied_overrides:
            self.current_instance_type = applied_overrides[self.service_id]

        profile = INSTANCE_PROFILES[self.current_instance_type]
        capacity = profile["capacity"]

        # Effective utilization = workload spread over current capacity
        noise = random.uniform(-3, 3)
        effective_cpu = max(0.5, min(99, (self.base_load * 4.0 / capacity) + noise))
        effective_mem = max(0.5, min(99, effective_cpu * random.uniform(0.8, 1.3)))

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service_id": self.service_id,
            "cpu_utilization_pct": round(effective_cpu, 2),
            "memory_utilization_pct": round(effective_mem, 2),
            "request_count": int(max(0, random.gauss(self.base_load * 20, 50))),
            "network_egress_mb": round(random.uniform(1, 500), 2),
            "current_instance_type": self.current_instance_type,
            "hourly_cost_usd": profile["hourly_cost_usd"],
        }


def main():
    producer = KafkaProducer(
        bootstrap_servers=[KAFKA_BROKER],
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )

    services = [ServiceState(name) for name in SERVICE_NAMES]
    print(f"[telemetry] streaming {len(services)} simulated services to '{TOPIC}' "
          f"on {KAFKA_BROKER} ... Ctrl+C to stop")

    try:
        while True:
            overrides = load_applied_resizes()
            for svc in services:
                payload = svc.tick(overrides)
                producer.send(TOPIC, value=payload)
            producer.flush()
            print(f"[telemetry] tick @ {datetime.now(timezone.utc).isoformat()} "
                  f"-> {len(services)} records sent")
            time.sleep(5)
    except KeyboardInterrupt:
        print("\n[telemetry] stopped.")


if __name__ == "__main__":
    main()
