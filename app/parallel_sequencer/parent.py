"""Merge child sequencers' ordered requests using precedence probabilities."""

from collections.abc import Iterable, Iterator
from heapq import heapify, heappop, heapreplace
from math import sqrt

from ..beyond_lamport import Message, NoisyTimestamp, OrderingProbabilityModel
from .models import OrderedRequestForParent


_PROBABILITY_MODEL = OrderingProbabilityModel()


def parent_clock_message(request: OrderedRequestForParent) -> Message:
    """Represent a request in the parent clock for the existing probability model."""
    offset = request.parent_user_offset
    offset_ns = int(offset.average_offset_seconds * 1_000_000_000)
    return Message(
        message_id=request.message_id,
        timestamp=NoisyTimestamp(
            client_id=offset.user_id,
            local_time=request.client_timestamp_ns + offset_ns,
            uncertainty=sqrt(offset.variance_offset_seconds_squared) * 1_000_000_000,
        ),
    )


class _ChildHead:
    """The current request and remaining iterator for one child."""

    def __init__(
        self,
        child_index: int,
        request: OrderedRequestForParent,
        remaining: Iterator[OrderedRequestForParent],
    ) -> None:
        self.child_index = child_index
        self.request = request
        self.remaining = remaining
        self.message = parent_clock_message(request)

    def __lt__(self, other: "_ChildHead") -> bool:
        first_time = self.message.timestamp.local_time
        second_time = other.message.timestamp.local_time
        if first_time == second_time:
            # The model also gives zero in both directions for equal, certain times.
            return self.child_index < other.child_index

        probability = _PROBABILITY_MODEL.probability_before(self.message, other.message)
        if probability == 0.5:
            # A nonzero difference can round to 0.5 in the Gaussian CDF.
            return first_time < second_time
        return probability > 0.5


def merge_child_sequences(
    child_sequences: Iterable[Iterable[OrderedRequestForParent]],
) -> list[OrderedRequestForParent]:
    """Merge one ordered sequence per child while preserving each child's order."""
    heads: list[_ChildHead] = []
    for child_index, sequence in enumerate(child_sequences):
        remaining = iter(sequence)
        first = next(remaining, None)
        if first is not None:
            heads.append(_ChildHead(child_index, first, remaining))
    heapify(heads)

    merged: list[OrderedRequestForParent] = []
    while heads:
        head = heads[0]
        merged.append(head.request)
        following = next(head.remaining, None)
        if following is None:
            heappop(heads)
        else:
            heapreplace(heads, _ChildHead(head.child_index, following, head.remaining))
    return merged
