import argparse
import json
import sys
from argparse import Namespace
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Codex script直接起動でもapp packageをimportできるようにrepo rootを追加する。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.probe_client import collect_samples, normalize_base_url, post_json, run_warmup


def parse_args() -> Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--sequencer', default='http://127.0.0.1:8000')
    parser.add_argument('--clients', type=int, required=True)
    parser.add_argument('--samples', type=int, default=100)
    parser.add_argument('--warmup', type=int, default=5)
    parser.add_argument('--interval', type=float, default=0.1)
    parser.add_argument('--sigma-multiplier', type=float, default=2.0)
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


def run_client(base_url: str, client_id: str, samples: int, warmup: int, interval: float) -> dict | None:
    # Codex 1クライアント分のwarmupとprobe送信を実行する。
    run_warmup(base_url, warmup, interval)
    return collect_samples(base_url, client_id, samples, interval)


def freeze_experiment(base_url: str) -> dict:
    # Codex 全クライアントのprobe完了後に、差分布を一度だけ作らせる。
    return post_json(f'{base_url}/api/experiment/freeze', {})


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

    with ThreadPoolExecutor(max_workers=args.clients) as executor:
        futures = {
            executor.submit(
                run_client,
                base_url,
                f'client-{client_index}',
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


if __name__ == '__main__':
    main()
