import argparse
import json
import sys
from argparse import Namespace
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Codex script直接起動でもapp packageをimportできるようにrepo rootを追加する。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.demo_client import DemoClient
from app.probe_client import normalize_base_url, post_json


def parse_args() -> Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--sequencer', default='http://127.0.0.1:8000')
    parser.add_argument('--clients', type=int, required=True)
    parser.add_argument('--samples', type=int, default=100)
    parser.add_argument('--warmup', type=int, default=5)
    parser.add_argument('--interval', type=float, default=0.1)
    parser.add_argument('--sigma-multiplier', type=float, default=2.0)
    parser.add_argument('--messages-per-client', type=int, default=1)
    return parser.parse_args()


def start_experiment(base_url: str, client_count: int, samples: int, sigma_multiplier: float) -> dict:
    # Codex sequencer側のprobe収集状態を初期化し、期待クライアント数を登録する。
    return post_json(
        f'{base_url}/api/experiment/start',
        dict(
            client_count=client_count,
            samples_per_client=samples,
            sigma_multiplier=sigma_multiplier,
        ),
    )


def run_client(client: DemoClient, samples: int, warmup: int, interval: float) -> dict | None:
    # Codex 同じクライアントオブジェクトでwarmupとprobe送信を実行する。
    client.run_warmup(warmup, interval)
    return client.collect_samples(samples, interval)


def freeze_experiment(base_url: str) -> dict:
    # Codex 全クライアントのprobe完了後に、差分布を一度だけ作らせる。
    return post_json(f'{base_url}/api/experiment/freeze', {})


def send_request_message(client: DemoClient, title: str) -> dict:
    return client.send_request_message(title)


def estimate_request_message_times(base_url: str) -> dict:
    return post_json(f'{base_url}/api/request-messages/estimate-times', {})


def calculate_precedence_probabilities(base_url: str) -> dict:
    return post_json(f'{base_url}/api/request-messages/precedence-probabilities', {})


def calculate_topological_order(base_url: str) -> dict:
    return post_json(f'{base_url}/api/request-messages/topological-order', {})


def print_estimated_request_messages(response: dict) -> None:
    print()
    print('Request messages with estimated real times:')
    for message in response.get('request_messages', []):
        print(
            f"{message['message_id']} "
            f"client={message['client_id']} "
            f"client_ts={message['client_timestamp_ns']} "
            f"estimated_real_time_ns={message.get('estimated_real_time_ns')} "
            f"offset={message.get('average_offset_seconds')} "
            f"title={message.get('payload', {}).get('title')}"
        )


def print_precedence_probabilities(response: dict) -> None:
    print()
    print('Pairwise message precedence probabilities:')
    for probability in response.get('precedence_probabilities', []):
        print(
            '{} before {}: p={:.6f}'.format(
                probability['first_message_id'],
                probability['second_message_id'],
                probability['probability_first_before_second'],
            )
        )


def print_topological_order(response: dict) -> None:
    print()
    print('Topological message order:')
    for index, message in enumerate(response.get('ordered_messages', []), start=1):
        title = message.get('payload', {}).get('title')
        print(
            '{}: {} client={} estimated_real_time_ns={} title={}'.format(
                index,
                message['message_id'],
                message['client_id'],
                message.get('estimated_real_time_ns'),
                title,
            )
        )
    removed_edges = response.get('removed_edges', [])
    if removed_edges:
        print('Removed cycle edges:')
        for edge in removed_edges:
            print(
                '{} -> {} confidence={:.6f}'.format(
                    edge['from_message_id'],
                    edge['to_message_id'],
                    edge['confidence'],
                )
            )


def print_delta_distributions(freeze_response: dict) -> None:
    # Codex freeze後に計算されたクライアント組み合わせごとの差分布を見やすく表示する。
    print()
    print('Pairwise clock offset delta distributions:')
    for distribution in freeze_response.get('delta_distributions', []):
        from_client = distribution['from_client_id']
        to_client = distribution['to_client_id']
        mean_us = distribution['average_delta_seconds'] * 1_000_000
        stddev_us = distribution['stddev_delta_seconds'] * 1_000_000
        uncertainty_us = distribution['uncertainty_seconds'] * 1_000_000
        print(
            f'{from_client} -> {to_client}: '
            f'mean_delta={mean_us:.3f}us '
            f'stddev_delta={stddev_us:.3f}us '
            f'uncertainty={uncertainty_us:.3f}us'
        )

def main() -> None:
    args = parse_args()
    base_url = normalize_base_url(args.sequencer)
    print(json.dumps(start_experiment(base_url, args.clients, args.samples, args.sigma_multiplier), indent=2))
    clients = [DemoClient(base_url, f'client-{client_index}') for client_index in range(args.clients)]

    with ThreadPoolExecutor(max_workers=args.clients) as executor:
        futures = {
            executor.submit(
                run_client,
                clients[client_index],
                args.samples,
                args.warmup,
                args.interval,
            ): client_index
            for client_index in range(args.clients)
        }
        for future in as_completed(futures):
            client_index = futures[future]
            future.result()
            print(f'client-{client_index} done')

    freeze_response = freeze_experiment(base_url)
    print(json.dumps(freeze_response, indent=2))
    print_delta_distributions(freeze_response)

    with ThreadPoolExecutor(max_workers=args.clients * args.messages_per_client) as executor:
        futures = []
        for client_index in range(args.clients):
            client = clients[client_index]
            for message_index in range(args.messages_per_client):
                title = f'ticket-{client.client_id}-{message_index}'
                futures.append(
                    executor.submit(send_request_message, client, title)
                )

        for future in as_completed(futures):
            response = future.result()
            print(json.dumps(response, indent=2, ensure_ascii=False))

    estimated_response = estimate_request_message_times(base_url)
    print(json.dumps(estimated_response, indent=2, ensure_ascii=False))
    print_estimated_request_messages(estimated_response)

    precedence_response = calculate_precedence_probabilities(base_url)
    print(json.dumps(precedence_response, indent=2, ensure_ascii=False))
    print_precedence_probabilities(precedence_response)

    topological_response = calculate_topological_order(base_url)
    print(json.dumps(topological_response, indent=2, ensure_ascii=False))
    print_topological_order(topological_response)


if __name__ == '__main__':
    main()
