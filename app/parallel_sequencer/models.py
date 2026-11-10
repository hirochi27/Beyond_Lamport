"""Data passed from child sequencers to the parent sequencer."""


from dataclasses import dataclass


@dataclass(frozen=True)
class ParentUserOffsetDistribution:
    """Parameters of the Gaussian offset parent clock minus user clock."""

    child_id: str
    user_id: str
    average_offset_seconds: float
    variance_offset_seconds_squared: float
