from dataclasses import asdict
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.beyond_lamport import (
    ClockOffsetDistribution,
    ClockOffsetDeltaDistribution,
    ClockOffsetEstimator,
    ClockProbeSample,
)


app = FastAPI()
clock_offsets = ClockOffsetEstimator()


#POSTされたJSONを受け取るための型
class ClockProbeSampleRequest(BaseModel):
    client_id: str
    client_send_time_ns: int
    sequencer_receive_time_ns: int
    sequencer_send_time_ns: int
    client_receive_time_ns: int


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


@app.get("/api/health")
def health():
    return {"status": "ok"}


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


# Codex 実験開始時に指定するクライアント数とprobe回数を受け取る型。
class ExperimentStartRequest(BaseModel):
    client_count: int
    samples_per_client: int
    sigma_multiplier: float = 2.0


# Codex freeze済みの差分布dataclassをJSONへ変換する。
def delta_distribution_response(distribution: ClockOffsetDeltaDistribution) -> dict:
    return asdict(distribution)


def experiment_status() -> dict:
    # Codex probe収集が完了してfreeze可能か、現在の実験状態を返す。
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
    # Codex 新しい実験条件でprobe収集状態を初期化する。
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
    # Codex 全probeが揃った時点の差分布を一回だけ作り、保存済みを返す。
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
    # Codex freeze済みの差分布だけを返し、ここでは再計算しない。
    return dict(
        delta_distributions=[
            delta_distribution_response(distribution)
            for distribution in delta_distributions.values()
        ]
    )
