"""
telemetry_consumer.py

Consumes the 'cloud-telemetry' Kafka/Redpanda topic and writes each record
into the TimescaleDB hypertable. Run this alongside telemetry_generator.py.
"""

import json
import os
import sys

try:
    if os.environ.get("USE_MOCK_KAFKA", "1") == "1":
        raise ImportError()
    from kafka import KafkaConsumer
except ImportError:
    sys.path.append(os.path.dirname(__file__))
    from kafka_mock import MockKafkaConsumer as KafkaConsumer

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "db"))
import db_helper

KAFKA_BROKER = os.environ.get("KAFKA_BROKER", "localhost:9092")
TOPIC = "cloud-telemetry"

DB_CONFIG = dict(
    host=os.environ.get("DB_HOST", "localhost"),
    port=os.environ.get("DB_PORT", "5432"),
    dbname=os.environ.get("DB_NAME", "finops_telemetry"),
    user=os.environ.get("DB_USER", "finops"),
    password=os.environ.get("DB_PASSWORD", "finops"),
)

INSERT_SQL = """
INSERT INTO cloud_telemetry
    (time, service_id, cpu_utilization_pct, memory_utilization_pct,
     request_count, network_egress_mb, current_instance_type, hourly_cost_usd)
VALUES (%(timestamp)s, %(service_id)s, %(cpu_utilization_pct)s, %(memory_utilization_pct)s,
        %(request_count)s, %(network_egress_mb)s, %(current_instance_type)s, %(hourly_cost_usd)s)
"""


def main():
    conn, db_type = db_helper.get_db_connection(DB_CONFIG)
    cur = conn.cursor()

    consumer = KafkaConsumer(
        TOPIC,
        bootstrap_servers=[KAFKA_BROKER],
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        auto_offset_reset="latest",
        enable_auto_commit=True,
        group_id="finops-consumer-group",
    )

    print(f"[consumer] listening on '{TOPIC}', writing into database (type: {db_type})...")
    count = 0
    sql = db_helper.translate_sql(INSERT_SQL, db_type)
    for msg in consumer:
        record = msg.value
        cur.execute(sql, record)
        conn.commit()
        count += 1
        if count % 50 == 0:
            print(f"[consumer] inserted {count} records so far")


if __name__ == "__main__":
    main()
