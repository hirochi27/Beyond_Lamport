"""Request and clock offset data for hierarchical sequencing."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ParentUserOffsetDistribution:
    """Parameters of the Gaussian offset parent clock minus user clock."""

    child_id: str
    user_id: str
    average_offset_seconds: float
    variance_offset_seconds_squared: float


@dataclass(frozen=True)
class ChildRequest:
    """A request received by a child, with its original user timestamp."""

    message_id: str
    client_id: str
    client_timestamp_ns: int


@dataclass(frozen=True)
class OrderedRequestForParent:
    """A request in the child's output sequence, with its parent-clock offset."""

    message_id: str
    client_timestamp_ns: int
    parent_user_offset: ParentUserOffsetDistribution
