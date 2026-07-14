from dataclasses import dataclass
from math import erf, sqrt
from statistics import mean, median, stdev


@dataclass(frozen=True)
class NoisyTimestamp:
    client_id: str
    local_time: float
    uncertainty: float

    def earliest_likely_time(self) -> float:
        return self.local_time - self.uncertainty

    def latest_likely_time(self) -> float:
        return self.local_time + self.uncertainty


#クライアントからもらったプローブの情報を型付きオブジェクトに変更
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


##計算済みオフセット分布を入れるデータ型
@dataclass(frozen=True)
class ClockOffsetDistribution:
    client_id: str
    sample_count: int
    accepted_sample_count: int
    rejected_sample_count: int
    average_offset_seconds: float
    stddev_offset_seconds: float
    min_offset_seconds: float
    max_offset_seconds: float
    median_rtt_seconds: float
    rtt_outlier_threshold_seconds: float
    uncertainty_seconds: float


#クライアント時計のtimestampを、シーケンサ時計基準の推定時刻（現実世界でリクエストが発生した時刻）に変換する
def estimate_client_real_time_ns(client_timestamp_ns: int, average_offset_seconds: float,) -> int:
    client_real_time = client_timestamp_ns + int(average_offset_seconds * 1_000_000_000)
    print(client_real_time)
    return client_real_time



# Codex クライアント2つの時計オフセット差分布 Δθ = θ_j - θ_i を入れるデータ型
@dataclass(frozen=True)
class ClockOffsetDeltaDistribution:
    from_client_id: str
    to_client_id: str
    average_delta_seconds: float
    stddev_delta_seconds: float
    uncertainty_seconds: float


#クライアントごとのオフセット分布を作成する
#RTTの外れ値は除外
class ClockOffsetEstimator:
    def __init__(
        self,
        sigma_multiplier: float = 2.0,
        expected_client_count: int | None = None,
        samples_per_client: int | None = None,
    ):
        self.sigma_multiplier = sigma_multiplier
        self.expected_client_count = expected_client_count
        self.samples_per_client = samples_per_client
        self._samples_by_client: dict[str, list[ClockProbeSample]] = {}
        #各クライアント事のオフセット分布を記録
        self._offset_distributions_by_client: dict[str, ClockOffsetDistribution] = {}
        # Codex freeze後に全クライアントペアの差分布を保存して使い回す。
        self._delta_distributions: dict[tuple[str, str], ClockOffsetDeltaDistribution] | None = None
        self._frozen = False

    #sampleをクライアントIDごとのリストに保存し、分布を計算
    def add_sample(self, sample: ClockProbeSample) -> ClockOffsetDistribution:
        if self._frozen:
            raise ValueError()

        if (
            self.expected_client_count is not None
            and sample.client_id not in self._samples_by_client
            and len(self._samples_by_client) >= self.expected_client_count
        ):
            raise ValueError()

        samples = self._samples_by_client.setdefault(sample.client_id, [])
        samples.append(sample)

        distribution = self.distribution_for(sample.client_id)
        self._offset_distributions_by_client[sample.client_id] = distribution
        #print(f"offset分布の記録{self._offset_distributions_by_client}")

        return distribution

    def is_ready(self) -> bool:
        # Codex 指定した全クライアントから必要数のprobeが揃ったかを見る。
        if self.expected_client_count is None or self.samples_per_client is None:
            return False

        return (
            len(self._samples_by_client) == self.expected_client_count
            and all(
                len(samples) >= self.samples_per_client
                for samples in self._samples_by_client.values()
            )
        )

    def sample_counts_by_client(self) -> dict[str, int]:
        # Codex APIのstatus表示用に、各クライアントのprobe数だけを返す。
        return {
            client_id: len(samples)
            for client_id, samples in sorted(self._samples_by_client.items())
        }

    def is_frozen(self) -> bool:
        return self._frozen

    #外れ値を除外
    def distribution_for(self, client_id: str) -> ClockOffsetDistribution:
        samples = self._samples_by_client.get(client_id, [])
        if not samples:
            raise ValueError(f"no clock probe samples for client: {client_id}")


        #RTTの中央値も計算、中央値から二倍以上のズレがあるRTTの場合使わない
        rtts = [sample.round_trip_delay_seconds() for sample in samples]
        median_rtt = median(rtts)
        rtt_threshold = abs(median_rtt) * 2

        accepted_samples = [
            sample
            for sample in samples
            if abs(sample.round_trip_delay_seconds()) <= rtt_threshold
        ]
        if not accepted_samples:
            accepted_samples = samples

        offsets = [sample.offset_seconds() for sample in accepted_samples]
        stddev_offset = stdev(offsets) if len(offsets) > 1 else 0.0

        #計算された分布を入れるデータ型
        return ClockOffsetDistribution(
            client_id=client_id,
            sample_count=len(samples),
            accepted_sample_count=len(accepted_samples),
            rejected_sample_count=len(samples) - len(accepted_samples),
            #平均
            average_offset_seconds=mean(offsets),
            #標準偏差
            stddev_offset_seconds=stddev_offset,
            min_offset_seconds=min(offsets),
            max_offset_seconds=max(offsets),
            median_rtt_seconds=median_rtt,
            rtt_outlier_threshold_seconds=rtt_threshold,
            uncertainty_seconds=self.sigma_multiplier * stddev_offset,
        )

    def pairwise_delta_distributions(self) -> dict[tuple[str, str], ClockOffsetDeltaDistribution]:
        client_ids = sorted(self._samples_by_client)

        distributions = {
            client_id: self.distribution_for(client_id)
            for client_id in client_ids
        }

        # Codex クライアント同士の Δθ = θ_j - θ_i を全クライアント組み合わせで先に作る。
        # Codex 正規分布同士の差前提なので、平均は差、分散は和として合成する。
        return {
            (i, j): ClockOffsetDeltaDistribution(
                from_client_id=i,
                to_client_id=j,
                average_delta_seconds=(
                    distributions[j].average_offset_seconds
                    - distributions[i].average_offset_seconds
                ),
                stddev_delta_seconds=sqrt(
                    distributions[i].stddev_offset_seconds**2
                    + distributions[j].stddev_offset_seconds**2
                ),
                uncertainty_seconds=self.sigma_multiplier
                * sqrt(
                    distributions[i].stddev_offset_seconds**2
                    + distributions[j].stddev_offset_seconds**2
                ),
            )
            for i in client_ids
            for j in client_ids
            if i != j
        }


    def freeze_delta_distributions(self) -> dict[tuple[str, str], ClockOffsetDeltaDistribution]:
        # Codex probe完了後に一度だけ差分布を作り、以後の順序計算で再利用する。
        if not self.is_ready():
            raise ValueError()

        self._delta_distributions = self.pairwise_delta_distributions()
        self._frozen = True
        return self._delta_distributions

    def delta_distributions(self) -> dict[tuple[str, str], ClockOffsetDeltaDistribution]:
        # Codex freeze済みの差分布だけを返し、未確定の再計算を避ける。
        if self._delta_distributions is None:
            raise ValueError()
        return self._delta_distributions

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


#先行確率を保存するためのデータ型
@dataclass(frozen=True)
class PairwisePrecedenceProbability:
    first_message_id: str
    second_message_id: str
    probability_first_before_second: float


#グラフのエッジ（メッセージ間の有向エッジ: P = confidence）
@dataclass(frozen=True)
class PrecedenceEdge:
    from_message_id: str
    to_message_id: str
    confidence: float


#トポロジカルソートの結果をいれる箱
@dataclass(frozen=True)
class TopologicalOrdering:
    ordered_message_ids: list[str]
    kept_edges: list[PrecedenceEdge]
    removed_edges: list[PrecedenceEdge]


#2つのメッセージの前後関係を確立で判定するクラス
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


#各メッセージペアの先行確率Pを計算する
def build_pairwise_precedence_probabilities(
    messages: list[Message],
    model: OrderingProbabilityModel | None = None,
) -> list[PairwisePrecedenceProbability]:
    probability_model = model or OrderingProbabilityModel()
    probabilities: list[PairwisePrecedenceProbability] = []

    for first_index, first in enumerate(messages):
        for second in messages[first_index + 1:]:
            probabilities.append(
                PairwisePrecedenceProbability(
                    first_message_id=first.message_id,
                    second_message_id=second.message_id,
                    probability_first_before_second=(
                        probability_model.probability_before(first, second)
                    ),
                )
            )

    return probabilities


#各ペアの先行確率から、確率が高い向きの有向エッジ候補を作る（A→B:10％の場合、B→A：90％　にする）
def build_candidate_precedence_edges(
    probabilities: list[PairwisePrecedenceProbability],
) -> list[PrecedenceEdge]:
    edges: list[PrecedenceEdge] = []

    for probability in probabilities:
        p_first_before_second = probability.probability_first_before_second
        if p_first_before_second >= 0.5:
            edges.append(
                PrecedenceEdge(
                    from_message_id=probability.first_message_id,
                    to_message_id=probability.second_message_id,
                    confidence=p_first_before_second,
                )
            )
        else:
            edges.append(
                PrecedenceEdge(
                    from_message_id=probability.second_message_id,
                    to_message_id=probability.first_message_id,
                    confidence=1.0 - p_first_before_second,
                )
            )

    return edges


def _has_path(edges: list[PrecedenceEdge], start_id: str, goal_id: str) -> bool:
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge.from_message_id, []).append(edge.to_message_id)

    stack = [start_id]
    visited: set[str] = set()
    while stack:
        current_id = stack.pop()
        if current_id == goal_id:
            return True
        if current_id in visited:
            continue
        visited.add(current_id)
        stack.extend(adjacency.get(current_id, []))

    return False


#強いエッジから採用し、巡回する部分は、弱いエッジから外す
def make_acyclic_precedence_edges(
    edges: list[PrecedenceEdge],
) -> tuple[list[PrecedenceEdge], list[PrecedenceEdge]]:
    kept_edges: list[PrecedenceEdge] = []
    removed_edges: list[PrecedenceEdge] = []

    sorted_edges = sorted(
        edges,
        key=lambda edge: (
            -edge.confidence,
            edge.from_message_id,
            edge.to_message_id,
        ),
    )
    for edge in sorted_edges:
        if _has_path(kept_edges, edge.to_message_id, edge.from_message_id):
            removed_edges.append(edge)
        else:
            kept_edges.append(edge)

    return kept_edges, removed_edges


#サイクル（巡回）のないエッジ集合を使って、メッセージIDを前から順に並べる
def topological_sort_message_ids(
    message_ids: list[str],
    edges: list[PrecedenceEdge],
) -> list[str]:
    original_index = {
        message_id: index
        for index, message_id in enumerate(message_ids)
    }
    adjacency = {message_id: [] for message_id in message_ids}
    indegree = {message_id: 0 for message_id in message_ids}

    for edge in edges:
        if edge.from_message_id not in indegree or edge.to_message_id not in indegree:
            continue
        adjacency[edge.from_message_id].append(edge.to_message_id)
        indegree[edge.to_message_id] += 1

    ready = [
        message_id
        for message_id in message_ids
        if indegree[message_id] == 0
    ]
    ordered_message_ids: list[str] = []

    while ready:
        ready.sort(key=lambda message_id: original_index[message_id])
        current_id = ready.pop(0)
        ordered_message_ids.append(current_id)

        for next_id in adjacency[current_id]:
            indegree[next_id] -= 1
            if indegree[next_id] == 0:
                ready.append(next_id)

    if len(ordered_message_ids) != len(message_ids):
        raise ValueError('precedence graph still has a cycle')

    return ordered_message_ids


#トポロジカルソートを作る入口。確率リストからエッジを作って、DAG化、最終的な順序を返す
def build_topological_ordering(
    messages: list[Message],
    probabilities: list[PairwisePrecedenceProbability],
) -> TopologicalOrdering:
    candidate_edges = build_candidate_precedence_edges(probabilities)
    kept_edges, removed_edges = make_acyclic_precedence_edges(candidate_edges)
    ordered_message_ids = topological_sort_message_ids(
        [message.message_id for message in messages],
        kept_edges,
    )

    return TopologicalOrdering(
        ordered_message_ids=ordered_message_ids,
        kept_edges=kept_edges,
        removed_edges=removed_edges,
    )


#複数のメッセージを並び替える＆バッチにする
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
