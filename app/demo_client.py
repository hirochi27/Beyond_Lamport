import json
import time
from urllib import request


class DemoClient:
    def __init__(self, base_url: str, client_id: str, clock_offset_ns: int = 0):
        self.base_url = base_url.rstrip('/')
        self.client_id = client_id
        self.clock_offset_ns = clock_offset_ns

    def now_ns(self) -> int:
        return time.time_ns() + self.clock_offset_ns

    def get_json(self, path: str) -> dict:
        with request.urlopen(f"{self.base_url}{path}", timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_json(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(http_request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def collect_warmup_probe(self) -> None:
        self.get_json("/api/clock-probe")

    def collect_probe(self) -> dict:
        client_send_time_ns = self.now_ns()
        probe = self.get_json("/api/clock-probe")
        client_receive_time_ns = self.now_ns()
        sample = {
            "client_id": self.client_id,
            "client_send_time_ns": client_send_time_ns,
            "sequencer_receive_time_ns": probe["sequencer_receive_time_ns"],
            "sequencer_send_time_ns": probe["sequencer_send_time_ns"],
            "client_receive_time_ns": client_receive_time_ns,
        }
        return self.post_json("/api/clock-offset-samples", sample)

    def run_warmup(self, warmup_count: int, interval: float) -> None:
        for warmup_index in range(warmup_count):
            self.collect_warmup_probe()
            print(f"{self.client_id} warmup {warmup_index + 1}/{warmup_count}")
            time.sleep(interval)

    def collect_samples(self, sample_count: int, interval: float) -> dict | None:
        last_response = None
        for index in range(sample_count):
            response = self.collect_probe()
            last_response = response
            self.print_probe_response(index, sample_count, response)
            time.sleep(interval)
        return last_response

    def build_ticket_create_message(self, title: str) -> dict:
        true_created_time_ns = time.time_ns()
        return {
            "client_id": self.client_id,
            "client_timestamp_ns": self.now_ns(),
            "kind": "create_ticket",
            "payload": {
                "title": title,
                "true_created_time_ns": true_created_time_ns,
            },
        }

    def send_request_message(self, title: str) -> dict:
        return self.post_json(
            "/api/request-messages",
            self.build_ticket_create_message(title),
        )

    def print_probe_response(self, index: int, sample_count: int, response: dict) -> None:
        sample = response["sample"]
        distribution = response["distribution"]
        print(
            f"{self.client_id} {index + 1}/{sample_count} "
            f"offset={sample['offset_seconds'] * 1_000_000:.3f}us "
            f"rtt={sample['round_trip_delay_seconds'] * 1_000_000:.3f}us "
            f"average={distribution['average_offset_seconds'] * 1_000_000:.3f}us "
            f"stddev={distribution['stddev_offset_seconds'] * 1_000_000:.3f}us "
            f"median_rtt={distribution['median_rtt_seconds'] * 1_000_000:.3f}us "
            f"rejected={distribution['rejected_sample_count']}"
        )
