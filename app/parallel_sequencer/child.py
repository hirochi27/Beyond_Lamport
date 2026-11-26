"""Order a child sequencer's requests and compose parent-clock offsets."""
###########親↔子のオフセット分布作成###########

import argparse
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from math import isfinite

from .. import probe_client
from ..beyond_lamport import (
    ClockOffsetDistribution,
    ClockOffsetEstimator,
    ClockProbeSample,
    Message,
    NoisyTimestamp,
    build_pairwise_precedence_probabilities,
    build_topological_ordering,
)
from .models import ChildRequest, OrderedRequestForParent, ParentUserOffsetDistribution


def measure_parent_clock_offset(
    parent_url: str,
    child_id: str,
    *,
    sample_count: int = 100,
    warmup_count: int = 5,
    interval: float = 0.1,
) -> ClockOffsetDistribution:
    """Estimate parent clock minus child clock from a fresh set of probes."""
    if sample_count <= 0:
        raise ValueError("samples must be positive")
    if warmup_count < 0:
        raise ValueError("warmup must be nonnegative")
    if not isfinite(interval) or interval < 0:
        raise ValueError("interval must be finite and nonnegative")

    base_url = probe_client.normalize_base_url(parent_url)
    for _ in range(warmup_count):
        probe_client.collect_warmup_probe(base_url)
        time.sleep(interval)

    estimator = ClockOffsetEstimator()
    for index in range(sample_count):
        sample = probe_client.capture_probe_sample(base_url, child_id)
        distribution = estimator.add_sample(ClockProbeSample(**sample))
        if index + 1 < sample_count:
            time.sleep(interval)
    return distribution


#####親↔子と子↔ユーザーの分布を重ね合わせる#####
def compose_parent_user_offset(
    parent_child: ClockOffsetDistribution,
    child_user: ClockOffsetDistribution,
) -> ParentUserOffsetDistribution:
    """Add independent parent-minus-child and child-minus-user offsets."""
    return ParentUserOffsetDistribution(
        child_id=parent_child.client_id,
        user_id=child_user.client_id,
        average_offset_seconds=(
            parent_child.average_offset_seconds + child_user.average_offset_seconds
        ),
        variance_offset_seconds_squared=(
            parent_child.stddev_offset_seconds**2 + child_user.stddev_offset_seconds**2
        ),
    )


########子の担当分のリクエストを並び替え、親に渡すためのリストにする########
def order_child_requests(
    requests: Sequence[ChildRequest],
    parent_child: ClockOffsetDistribution,
    child_user_offsets: Mapping[str, ClockOffsetDistribution],
) -> list[OrderedRequestForParent]:
    """Order requests in the child clock and attach parent-clock offsets."""
    requests_by_id: dict[str, ChildRequest] = {}
    used_offsets: dict[str, ClockOffsetDistribution] = {}
    messages: list[Message] = []
    for request in requests:
        if request.message_id in requests_by_id:
            raise ValueError(f"duplicate request ID: {request.message_id}")

        distribution = child_user_offsets.get(request.client_id)
        if distribution is None:
            raise ValueError(f"no child-user offset for user: {request.client_id}")
        if distribution.client_id != request.client_id:
            raise ValueError(f"child-user offset belongs to a different user: {request.client_id}")

        requests_by_id[request.message_id] = request
        used_offsets[request.client_id] = distribution
        # Match the sequential API's nanosecond conversion for means and sigmas.
        offset_ns = int(distribution.average_offset_seconds * 1_000_000_000)
        messages.append(Message(
            message_id=request.message_id,
            timestamp=NoisyTimestamp(
                client_id=request.client_id,
                local_time=request.client_timestamp_ns + offset_ns,
                uncertainty=distribution.stddev_offset_seconds * 1_000_000_000,
            ),
        ))

    probabilities = build_pairwise_precedence_probabilities(messages)
    ordering = build_topological_ordering(messages, probabilities)
    parent_user_offsets = {
        client_id: compose_parent_user_offset(parent_child, distribution)
        for client_id, distribution in used_offsets.items()
    }
    ordered_requests = [requests_by_id[message_id] for message_id in ordering.ordered_message_ids]
    return [
        OrderedRequestForParent(
            message_id=request.message_id,
            client_timestamp_ns=request.client_timestamp_ns,
            parent_user_offset=parent_user_offsets[request.client_id],
        )
        for request in ordered_requests
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure the parent clock offset and optionally compose user offsets.",
    )
    parser.add_argument("--parent", default="http://127.0.0.1:8001", help="Parent server URL")
    parser.add_argument("--child-id", required=True, help="ID of the probing child")
    parser.add_argument("--samples", type=int, default=100, help="Number of measured probes")
    parser.add_argument("--warmup", type=int, default=5, help="Number of discarded warmup probes")
    parser.add_argument("--interval", type=float, default=0.1, help="Seconds between probes")
    parser.add_argument(
        "--child-sequencer",
        help="Child server URL to fetch its measured user offsets and compose parent-user offsets",
    )
    args = parser.parse_args()

    try:
        distribution = measure_parent_clock_offset(
            args.parent,
            args.child_id,
            sample_count=args.samples,
            warmup_count=args.warmup,
            interval=args.interval,
        )
    except ValueError as error:
        parser.error(str(error))
    if args.child_sequencer is None:
        print(json.dumps(asdict(distribution), indent=2))
        return

    child_url = probe_client.normalize_base_url(args.child_sequencer)
    measured_users = probe_client.get_json(f"{child_url}/api/clock-offset-distributions")
    if not measured_users["distributions"]:
        parser.error("no user clock offsets on the child server; run user probes first")
    parent_user_distributions = [
        asdict(compose_parent_user_offset(distribution, ClockOffsetDistribution(**user)))
        for user in measured_users["distributions"]
    ]
    print(json.dumps({
        "parent_child_distribution": asdict(distribution),
        "parent_user_distributions": parent_user_distributions,
    }, indent=2))


if __name__ == "__main__":
    main()
