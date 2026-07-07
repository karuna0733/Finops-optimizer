import json
import os
import time

class MockKafkaProducer:
    def __init__(self, bootstrap_servers=None, value_serializer=None, **kwargs):
        self.queue_file = os.path.join(
            os.path.dirname(__file__), "..", "sandbox", "kafka_queue.jsonl"
        )
        os.makedirs(os.path.dirname(self.queue_file), exist_ok=True)
        self.value_serializer = value_serializer
        print(f"[mock-kafka] Producer initialized using queue file: {self.queue_file}")

    def send(self, topic, value):
        serialized = self.value_serializer(value) if self.value_serializer else json.dumps(value).encode('utf-8')
        with open(self.queue_file, "ab") as f:
            f.write(serialized + b"\n")

    def flush(self):
        pass

class MockKafkaConsumer:
    def __init__(self, topic=None, bootstrap_servers=None, value_deserializer=None, **kwargs):
        self.queue_file = os.path.join(
            os.path.dirname(__file__), "..", "sandbox", "kafka_queue.jsonl"
        )
        os.makedirs(os.path.dirname(self.queue_file), exist_ok=True)
        self.value_deserializer = value_deserializer
        print(f"[mock-kafka] Consumer initialized listening on queue file: {self.queue_file}")

    def __iter__(self):
        while True:
            if os.path.exists(self.queue_file) and os.path.getsize(self.queue_file) > 0:
                try:
                    with open(self.queue_file, "r+b") as f:
                        lines = f.readlines()
                        f.seek(0)
                        f.truncate()
                    
                    for line in lines:
                        if not line.strip():
                            continue
                        class Message:
                            def __init__(self, val, deserializer):
                                self.value = deserializer(val) if deserializer else json.loads(val.decode('utf-8'))
                        yield Message(line.strip(), self.value_deserializer)
                except Exception as e:
                    print(f"[mock-kafka] error reading queue: {e}")
            time.sleep(0.5)
