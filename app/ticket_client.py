import argparse
import json
import time
from argparse import Namespace
from urllib import request


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


def parse_args() -> Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sequencer", default="http://127.0.0.1:8000")
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--title", required=True)
    return parser.parse_args()


def normalize_base_url(sequencer_url: str) -> str:
    return sequencer_url.rstrip("/")


def build_ticket_create_message(client_id: str, title: str) -> dict:
    return {
        "client_id": client_id,
        "client_timestamp_ns": time.time_ns(),
        "kind": "create_ticket",
        "payload": {"title": title},
    }


def main() -> None:
    args = parse_args()
    base_url = normalize_base_url(args.sequencer)
    message = build_ticket_create_message(args.client_id, args.title)
    response = post_json(f"{base_url}/api/request-messages", message)
    print(json.dumps(response, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
