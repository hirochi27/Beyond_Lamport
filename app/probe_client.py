import argparse
import json
import time
from urllib import request

###クライアント側の操作/プローブの計測


def get_json(url: str) -> dict:
    with request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    http_request = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(http_request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def collect_probe(base_url: str, client_id: str) -> dict:
    client_send_time_ns = time.time_ns()
    probe = get_json(f"{base_url}/api/clock-probe")
    client_receive_time_ns = time.time_ns()

    sample = {
        "client_id": client_id,
        "client_send_time_ns": client_send_time_ns,
        "sequencer_receive_time_ns": probe["sequencer_receive_time_ns"],
        "sequencer_send_time_ns": probe["sequencer_send_time_ns"],
        "client_receive_time_ns": client_receive_time_ns,
    }
    return post_json(f"{base_url}/api/clock-offset-samples", sample)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sequencer", default="http://127.0.0.1:8000")
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--samples", type=int, default=50)
    parser.add_argument("--interval", type=float, default=0.1)
    args = parser.parse_args()

    base_url = args.sequencer.rstrip("/")
    latest = None
    for index in range(args.samples):
        latest = collect_probe(base_url, args.client_id)
        sample = latest["sample"]
        distribution = latest["distribution"]
        print(
            f"{index + 1}/{args.samples} "
            f"offset={sample['offset_seconds'] * 1_000_000:.3f}us "
            f"rtt={sample['round_trip_delay_seconds'] * 1_000_000:.3f}us "
            f"mean={distribution['mean_offset_seconds'] * 1_000_000:.3f}us "
            f"stddev={distribution['stddev_offset_seconds'] * 1_000_000:.3f}us"
        )
        time.sleep(args.interval)

    if latest is not None:
        print(json.dumps(latest["distribution"], indent=2))


if __name__ == "__main__":
    main()
