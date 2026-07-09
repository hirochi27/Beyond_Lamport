from dataclasses import dataclass
from math import erf, sqrt
from statistics import mean, stdev


@dataclass(frozen=True)
class NoisyTimestamp:
    client_id: str
    local_time: float
    uncertainty: float

    def earliest_likely_time(self) -> float:
        return self.local_time - self.uncertainty

    def latest_likely_time(self) -> float:
        return self.local_time + self.uncertainty


##クライアントサーバ間で、プローブを計測
@dataclass(frozen=True)
class ClockProbeSample:
    client_id: str
    client_send_time_ns: int
    sequencer_receive_time_ns: int
    sequencer_send_time_ns: int
    client_receive_time_ns: int

    def offset_seconds(self) -> float:
        first_leg = self.sequencer_receive_time_ns - self.client_send_time_ns
        second_leg = self.sequencer_send_time_ns - self.client_receive_time_ns
        return ((first_leg + second_leg) / 2) / 1_000_000_000

    def round_trip_delay_seconds(self) -> float:
        client_elapsed = self.client_receive_time_ns - self.client_send_time_ns
        sequencer_elapsed = self.sequencer_send_time_ns - self.sequencer_receive_time_ns
        return (client_elapsed - sequencer_elapsed) / 1_000_000_000


##サーバクライアント間で、クロックオフセット分布を作成する
@dataclass(frozen=True)
class ClockOffsetDistribution:
    client_id: str
    sample_count: int
    mean_offset_seconds: float
    stddev_offset_seconds: float
    min_offset_seconds: float
    max_offset_seconds: float
    uncertainty_seconds: float


class ClockOffsetEstimator:
    def __init__(self, sigma_multiplier: float = 2.0):
        self.sigma_multiplier = sigma_multiplier
        self._samples_by_client: dict[str, list[ClockProbeSample]] = {}

    def add_sample(self, sample: ClockProbeSample) -> ClockOffsetDistribution:
        samples = self._samples_by_client.setdefault(sample.client_id, [])
        samples.append(sample)
        return self.distribution_for(sample.client_id)

    def distribution_for(self, client_id: str) -> ClockOffsetDistribution:
        samples = self._samples_by_client.get(client_id, [])
        if not samples:
            raise ValueError(f"no clock probe samples for client: {client_id}")

        offsets = [sample.offset_seconds() for sample in samples]
        stddev_offset = stdev(offsets) if len(offsets) > 1 else 0.0

        return ClockOffsetDistribution(
            client_id=client_id,
            sample_count=len(samples),
            mean_offset_seconds=mean(offsets),
            stddev_offset_seconds=stddev_offset,
            min_offset_seconds=min(offsets),
            max_offset_seconds=max(offsets),
            uncertainty_seconds=self.sigma_multiplier * stddev_offset,
        )

    def distributions(self) -> list[ClockOffsetDistribution]:
        return [
            self.distribution_for(client_id)
            for client_id in sorted(self._samples_by_client)
        ]


@dataclass(frozen=True)
class Message:
    message_id: str
    timestamp: NoisyTimestamp
    payload: str | None = None


@dataclass(frozen=True)
class OrderingDecision:
    before: Message
    after: Message
    probability: float
    confident: bool


class OrderingProbabilityModel:
    def __init__(self, confidence_threshold: float = 0.95):
        self.confidence_threshold = confidence_threshold

    def probability_before(self, first: Message, second: Message) -> float:
        first_time = first.timestamp.local_time
        second_time = second.timestamp.local_time
        combined_uncertainty = sqrt(
            first.timestamp.uncertainty**2 + second.timestamp.uncertainty**2
        )

        if combined_uncertainty == 0:
            return 1.0 if first_time < second_time else 0.0

        z_score = (second_time - first_time) / combined_uncertainty
        return self._normal_cdf(z_score)

    def compare(self, first: Message, second: Message) -> OrderingDecision:
        probability = self.probability_before(first, second)

        if probability >= self.confidence_threshold:
            return OrderingDecision(first, second, probability, True)

        reverse_probability = 1.0 - probability
        if reverse_probability >= self.confidence_threshold:
            return OrderingDecision(second, first, reverse_probability, True)

        return OrderingDecision(first, second, probability, False)

    def _normal_cdf(self, value: float) -> float:
        return 0.5 * (1.0 + erf(value / sqrt(2.0)))


class FairSequencer:
    def __init__(self, model: OrderingProbabilityModel | None = None):
        self.model = model or OrderingProbabilityModel()

    def sequence(self, messages: list[Message]) -> list[list[Message]]:
        remaining = sorted(messages, key=lambda message: message.timestamp.local_time)
        batches: list[list[Message]] = []

        while remaining:
            current = remaining.pop(0)
            batch = [current]
            next_remaining: list[Message] = []

            for candidate in remaining:
                decision = self.model.compare(current, candidate)
                if decision.confident:
                    next_remaining.append(candidate)
                else:
                    batch.append(candidate)

            batches.append(batch)
            remaining = next_remaining

        return batches
