from dataclasses import asdict
import time
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.beyond_lamport import (
    ClockOffsetDistribution,
    ClockOffsetDeltaDistribution,
    ClockOffsetEstimator,
    ClockProbeSample,
    Message,
    NoisyTimestamp,
    PairwisePrecedenceProbability,
    PrecedenceEdge,
    TopologicalOrdering,
    build_pairwise_precedence_probabilities,
    build_topological_ordering,
    estimate_client_real_time_ns,
)


app = FastAPI()
clock_offsets = ClockOffsetEstimator()
request_messages: list[dict] = []
#各メッセージペアの先行確率を保存
pairwise_precedence_probabilities: list[dict] = []
topological_ordering: dict | None = None


#POSTされたJSONを受け取るための型
class ClockProbeSampleRequest(BaseModel):
    client_id: str
    client_send_time_ns: int
    sequencer_receive_time_ns: int
    sequencer_send_time_ns: int
    client_receive_time_ns: int


#メッセージを受け取った時
class RequestMessageRequest(BaseModel):
    client_id: str
    client_timestamp_ns: int
    kind: str = "create_ticket"
    payload: dict = Field(default_factory=dict)


def build_request_message(request: RequestMessageRequest) -> dict:
    return {
        "message_id": str(uuid.uuid4()),
        "client_id": request.client_id,
        "client_timestamp_ns": request.client_timestamp_ns,
        "kind": request.kind,
        "payload": request.payload,
        "sequencer_receive_time_ns": time.time_ns(),
    }


def build_probe_sample(request: ClockProbeSampleRequest) -> ClockProbeSample:
    #Convert the HTTP request body into the domain object used for calculations.
    return ClockProbeSample(**request.model_dump())


#今回の sample 結果と最新 distribution を JSON response にまとめる。
def build_probe_response(
    sample: ClockProbeSample,
    distribution: ClockOffsetDistribution,
) -> dict:
    #Return both the per-probe calculation and the current distribution snapshot.
    return {
        "sample": {
            **asdict(sample),
            "offset_seconds": sample.offset_seconds(),
            "round_trip_delay_seconds": sample.round_trip_delay_seconds(),
        },
        "distribution": asdict(distribution),
    }


def distribution_response(distribution: ClockOffsetDistribution) -> dict:
    return asdict(distribution)


def precedence_probability_response(
    probability: PairwisePrecedenceProbability,
) -> dict:
    return asdict(probability)


def precedence_edge_response(edge: PrecedenceEdge) -> dict:
    return asdict(edge)


def topological_ordering_response(ordering: TopologicalOrdering) -> dict:
    ordered_messages = [
        message
        for message_id in ordering.ordered_message_ids
        for message in request_messages
        if message['message_id'] == message_id
    ]
    return {
        'ordered_message_ids': ordering.ordered_message_ids,
        'ordered_messages': ordered_messages,
        'kept_edges': [
            precedence_edge_response(edge)
            for edge in ordering.kept_edges
        ],
        'removed_edges': [
            precedence_edge_response(edge)
            for edge in ordering.removed_edges
        ],
    }


def message_to_ordering_message(message: dict) -> Message:
    distribution = clock_offsets.distribution_for(message['client_id'])
    if 'estimated_real_time_ns' not in message:
        message['estimated_real_time_ns'] = estimate_client_real_time_ns(
            message['client_timestamp_ns'],
            distribution.average_offset_seconds,
        )
        message['average_offset_seconds'] = distribution.average_offset_seconds

    message['uncertainty_ns'] = int(distribution.uncertainty_seconds * 1_000_000_000)

    return Message(
        message_id=message['message_id'],
        timestamp=NoisyTimestamp(
            client_id=message['client_id'],
            local_time=message['estimated_real_time_ns'],
            uncertainty=message['uncertainty_ns'],
        ),
        payload=message.get('kind'),
    )


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/request-messages")
def add_request_message(request: RequestMessageRequest):
    message = build_request_message(request)
    print(message)
    request_messages.append(message)
    return {"request_message": message}


@app.get("/api/request-messages")
def get_request_messages():
    return {"request_messages": request_messages}


#保存したリクエストメッセージに、推定リクエスト発生時刻を追加して保存する
@app.post('/api/request-messages/estimate-times')
def estimate_and_store_request_message_times():
    for message in request_messages:
        try:
            message_to_ordering_message(message)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {'request_messages': request_messages}


@app.post('/api/request-messages/precedence-probabilities')
def calculate_and_store_precedence_probabilities():
    global pairwise_precedence_probabilities
    try:
        ordering_messages = [
            message_to_ordering_message(message)
            for message in request_messages
        ]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    pairwise_precedence_probabilities = [
        precedence_probability_response(probability)
        for probability in build_pairwise_precedence_probabilities(ordering_messages)
    ]
    return {
        'precedence_probabilities': pairwise_precedence_probabilities,
    }


@app.get('/api/request-messages/precedence-probabilities')
def get_precedence_probabilities():
    return {
        'precedence_probabilities': pairwise_precedence_probabilities,
    }


@app.post('/api/request-messages/topological-order')
def calculate_and_store_topological_order():
    global pairwise_precedence_probabilities, topological_ordering
    try:
        ordering_messages = [
            message_to_ordering_message(message)
            for message in request_messages
        ]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    probabilities = build_pairwise_precedence_probabilities(ordering_messages)
    pairwise_precedence_probabilities = [
        precedence_probability_response(probability)
        for probability in probabilities
    ]
    topological_ordering = topological_ordering_response(
        build_topological_ordering(ordering_messages, probabilities)
    )
    request_messages[:] = topological_ordering['ordered_messages']
    return topological_ordering


@app.get('/api/request-messages/topological-order')
def get_topological_order():
    if topological_ordering is None:
        raise HTTPException(status_code=404, detail='topological order is not calculated yet')
    return topological_ordering


#シーケンサが受け取った時間と送った時間を返す
@app.get("/api/clock-probe")
def clock_probe():
    receive_time_ns = time.time_ns()
    send_time_ns = time.time_ns()
    return {
        "sequencer_receive_time_ns": receive_time_ns,
        "sequencer_send_time_ns": send_time_ns,
    }


#probeのtimestampをうけとって、分布を更新し、返す
@app.post("/api/clock-offset-samples")
#受け取った値はClockProbeSampleRequestを通してから計算
def add_clock_offset_sample(request: ClockProbeSampleRequest):
    #クライアントからもらったプローブの情報をClockProveSampleに変換
    sample = build_probe_sample(request)
    #sampleを保存し、sampleから最新の分布の情報を作成
    try:
        distribution = clock_offsets.add_sample(sample)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return build_probe_response(sample, distribution)


@app.get("/api/clock-offset-distributions")
def clock_offset_distributions():
    distributions = clock_offsets.distributions()
    return {
        "distributions": [
            distribution_response(distribution)
            for distribution in distributions
        ]
    }


@app.get("/api/clock-offset-distributions/{client_id}")
def clock_offset_distribution(client_id: str):
    try:
        distribution = clock_offsets.distribution_for(client_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return distribution_response(distribution)



# 実験開始時に指定するクライアント数とprobe回数を受け取る型。
class ExperimentStartRequest(BaseModel):
    client_count: int
    samples_per_client: int
    sigma_multiplier: float = 2.0


# freeze済みの差分布dataclassをJSONへ変換する。
def delta_distribution_response(distribution: ClockOffsetDeltaDistribution) -> dict:
    return asdict(distribution)


def experiment_status() -> dict:
    # probe収集が完了してfreeze可能か、現在の実験状態を返す。
    return dict(
        expected_client_count=clock_offsets.expected_client_count,
        samples_per_client=clock_offsets.samples_per_client,
        ready=clock_offsets.is_ready(),
        frozen=clock_offsets.is_frozen(),
        sample_counts=clock_offsets.sample_counts_by_client(),
    )


@app.post("/api/experiment/start")
def start_experiment(request: ExperimentStartRequest):
    global clock_offsets
    if request.client_count <= 0 or request.samples_per_client <= 0:
        raise HTTPException(status_code=400)
    # 新しい実験条件でprobe収集状態を初期化する。
    clock_offsets = ClockOffsetEstimator(
        sigma_multiplier=request.sigma_multiplier,
        expected_client_count=request.client_count,
        samples_per_client=request.samples_per_client,
    )
    return experiment_status()


@app.get("/api/experiment/status")
def get_experiment_status():
    return experiment_status()


@app.post("/api/experiment/freeze")
def freeze_experiment():
    try:
        delta_distributions = clock_offsets.freeze_delta_distributions()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # 全probeが揃った時点の差分布を一回だけ作り、保存済みを返す。
    return dict(
        delta_distributions=[
            delta_distribution_response(distribution)
            for distribution in delta_distributions.values()
        ],
        **experiment_status(),
    )


@app.get("/api/clock-offset-delta-distributions")
def clock_offset_delta_distributions():
    try:
        delta_distributions = clock_offsets.delta_distributions()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # freeze済みの差分布だけを返し、ここでは再計算しない。
    return dict(
        delta_distributions=[
            delta_distribution_response(distribution)
            for distribution in delta_distributions.values()
        ]
    )
