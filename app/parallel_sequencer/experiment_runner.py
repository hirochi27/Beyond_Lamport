"""Compare request orders using simulated clock probes."""

import argparse
import os
from concurrent.futures import ProcessPoolExecutor
from itertools import combinations
from math import isfinite
from random import Random

from ..beyond_lamport import (
    ClockOffsetDistribution,
    ClockOffsetEstimator,
    ClockProbeSample,
    build_pairwise_precedence_probabilities,
    build_topological_ordering,
)
from .child import order_child_requests
from .models import ChildRequest, OrderedRequestForParent
from .parent import merge_child_sequences, parent_clock_message


NS_PER_MS = 1_000_000


def estimate_simulated_offset(
    peer_id: str,
    mean_ns: int,
    jitter_ns: float,
    samples: int,
    rng: Random,
) -> ClockOffsetDistribution:
    estimator = ClockOffsetEstimator()
    half_rtt_ns = NS_PER_MS
    for _ in range(samples):
        # Model symmetric transit time and a sampled responder-minus-initiator offset.
        offset_ns = round(rng.gauss(mean_ns, jitter_ns))
        responder_time_ns = half_rtt_ns + offset_ns
        distribution = estimator.add_sample(ClockProbeSample(
            client_id=peer_id,
            client_send_time_ns=0,
            sequencer_receive_time_ns=responder_time_ns,
            sequencer_send_time_ns=responder_time_ns,
            client_receive_time_ns=2 * half_rtt_ns,
        ))
    return distribution


def order_in_child_process(
    requests: list[ChildRequest],
    parent_child: ClockOffsetDistribution,
    child_user_offsets: dict[str, ClockOffsetDistribution],
) -> tuple[int, list[OrderedRequestForParent]]:
    return os.getpid(), order_child_requests(requests, parent_child, child_user_offsets)


def order_sequential_baseline(
    requests: list[OrderedRequestForParent],
) -> list[OrderedRequestForParent]:
    """Run the single-sequencer linear ordering with one shared clock reference."""
    messages = [parent_clock_message(request) for request in requests]
    probabilities = build_pairwise_precedence_probabilities(messages)
    ordering = build_topological_ordering(messages, probabilities)
    by_id = {request.message_id: request for request in requests}
    return [by_id[message_id] for message_id in ordering.ordered_message_ids]


def print_comparison(
    true_order: list[ChildRequest],
    merged_order: list[OrderedRequestForParent],
    baseline_order: list[OrderedRequestForParent] | None,
) -> None:
    raw_order = sorted(true_order, key=lambda request: request.client_timestamp_ns)
    true_rank = {request.message_id: rank for rank, request in enumerate(true_order, 1)}
    rank_width = max(2, len(str(len(true_order))))
    request_labels = {
        request.message_id: (
            f"R{true_rank[request.message_id]:0{rank_width}d}/"
            f"{request.parent_user_offset.child_id}"
        )
        for request in merged_order
    }
    columns = [
        ("True creation", "True creation", [request.message_id for request in true_order]),
        ("Raw user clock", "Before ordering", [request.message_id for request in raw_order]),
        ("After parent merge", "After parent merge", [request.message_id for request in merged_order]),
    ]
    if baseline_order is not None:
        columns.append((
            "Sequential baseline",
            "Sequential baseline",
            [request.message_id for request in baseline_order],
        ))

    column_width = max(
        max(len(heading) for heading, _, _ in columns),
        max(map(len, request_labels.values()), default=0),
    )
    print("Rxx = true creation rank; child-N = assigned child")
    headings = "  ".join(f"{heading:<{column_width}}" for heading, _, _ in columns)
    print(f"{'Rank':>4}  {headings.rstrip()}")
    for rank, row in enumerate(zip(*(order for _, _, order in columns), strict=True), 1):
        cells = "  ".join(f"{request_labels[message_id]:<{column_width}}" for message_id in row)
        print(f"{rank:4}  {cells.rstrip()}")

    print()
    total_pairs = len(true_order) * (len(true_order) - 1) // 2
    for _, label, order in columns[1:]:
        ranks = [true_rank[message_id] for message_id in order]
        inversions = sum(first > second for first, second in combinations(ranks, 2))
        print(f"{label}: {inversions}/{total_pairs} inverted pairs vs. true creation")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--children", type=int, default=3, help="Number of child processes")
    parser.add_argument("--clients", type=int, default=6, help="Number of simulated users")
    parser.add_argument("--messages-per-client", type=int, default=1, help="Requests per user")
    parser.add_argument("--samples", type=int, default=20, help="Simulated probes per clock link")
    parser.add_argument(
        "--clock-jitter-stddev-ms", type=float, default=20.0,
        help="User timestamp and child-user probe offset jitter standard deviation in ms",
    )
    parser.add_argument(
        "--parent-child-jitter-stddev-ms", type=float, default=10.0,
        help="Parent-child probe offset jitter standard deviation in ms",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--compare-baseline", action="store_true",
        help="Also run the sequential DAG ordering with the same parent-clock offsets",
    )
    args = parser.parse_args()
    if not 1 <= args.children <= args.clients:
        parser.error("children must be positive and no greater than clients")
    if args.messages_per_client <= 0 or args.samples <= 0:
        parser.error("messages-per-client and samples must be positive")
    if not isfinite(args.clock_jitter_stddev_ms) or args.clock_jitter_stddev_ms < 0:
        parser.error("clock-jitter-stddev-ms must be finite and nonnegative")
    if not isfinite(args.parent_child_jitter_stddev_ms) or args.parent_child_jitter_stddev_ms < 0:
        parser.error("parent-child-jitter-stddev-ms must be finite and nonnegative")

    rng = Random(args.seed)
    user_jitter_ns = args.clock_jitter_stddev_ms * NS_PER_MS
    parent_child_jitter_ns = args.parent_child_jitter_stddev_ms * NS_PER_MS
    # The experiment's true clock is the parent clock.
    user_clock_offsets = [rng.randint(-150, 150) * NS_PER_MS for _ in range(args.clients)]
    requests_by_child: list[list[ChildRequest]] = [[] for _ in range(args.children)]
    true_order: list[ChildRequest] = []
    for message_number in range(1, args.messages_per_client + 1):
        client_order = list(range(args.clients))
        rng.shuffle(client_order)
        for client_index in client_order:
            true_time_ns = len(true_order) * 10 * NS_PER_MS
            user_id = f"user-{client_index + 1}"
            user_offset_ns = round(rng.gauss(user_clock_offsets[client_index], user_jitter_ns))
            request = ChildRequest(f"{user_id}/{message_number}", user_id, true_time_ns + user_offset_ns)
            true_order.append(request)
            requests_by_child[client_index % args.children].append(request)

    child_clock_offsets = [rng.randint(-100, 100) * NS_PER_MS for _ in range(args.children)]
    child_user_offsets: list[dict[str, ClockOffsetDistribution]] = [
        {} for _ in range(args.children)
    ]
    for client_index, user_offset_ns in enumerate(user_clock_offsets):
        child_index = client_index % args.children
        user_id = f"user-{client_index + 1}"
        child_user_offsets[child_index][user_id] = estimate_simulated_offset(
            user_id, child_clock_offsets[child_index] - user_offset_ns, user_jitter_ns, args.samples, rng,
        )
    parent_child_offsets = [
        estimate_simulated_offset(
            f"child-{index + 1}", -offset_ns, parent_child_jitter_ns, args.samples, rng,
        )
        for index, offset_ns in enumerate(child_clock_offsets)
    ]

    # Give each child task its own process, even when the request batches are small.
    with ProcessPoolExecutor(max_workers=args.children, max_tasks_per_child=1) as executor:
        children = [
            executor.submit(order_in_child_process, requests, parent_child, user_offsets)
            for requests, parent_child, user_offsets in zip(
                requests_by_child, parent_child_offsets, child_user_offsets, strict=True,
            )
        ]
        child_results = [child.result() for child in children]

    print(f"Simulated clocks (seed {args.seed}); parent PID {os.getpid()}")
    print(f"{args.clients} users, {len(true_order)} requests; {args.samples} probes per clock link")
    print(
        f"Probe offset jitter (stddev): parent-child {args.parent_child_jitter_stddev_ms:g} ms, "
        f"child-user {args.clock_jitter_stddev_ms:g} ms"
    )
    for index, (pid, sequence) in enumerate(child_results, 1):
        print(f"child-{index}: PID {pid}, {len(sequence)} requests")
    print()
    child_sequences = [sequence for _, sequence in child_results]
    merged_order = merge_child_sequences(child_sequences)
    baseline_order = None
    if args.compare_baseline:
        parent_requests = {
            request.message_id: request
            for sequence in child_sequences
            for request in sequence
        }
        baseline_input = [parent_requests[request.message_id] for request in true_order]
        baseline_order = order_sequential_baseline(baseline_input)
    print_comparison(true_order, merged_order, baseline_order)


if __name__ == "__main__":
    main()
