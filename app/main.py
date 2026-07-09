from dataclasses import asdict
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.beyond_lamport import ClockOffsetEstimator, ClockProbeSample


app = FastAPI()
clock_offsets = ClockOffsetEstimator()


class ClockProbeSampleRequest(BaseModel):
    client_id: str
    client_send_time_ns: int
    sequencer_receive_time_ns: int
    sequencer_send_time_ns: int
    client_receive_time_ns: int


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/clock-probe")
def clock_probe():
    receive_time_ns = time.time_ns()
    send_time_ns = time.time_ns()
    return {
        "sequencer_receive_time_ns": receive_time_ns,
        "sequencer_send_time_ns": send_time_ns,
    }


@app.post("/api/clock-offset-samples")
def add_clock_offset_sample(request: ClockProbeSampleRequest):
    sample = ClockProbeSample(**request.model_dump())
    distribution = clock_offsets.add_sample(sample)
    return {
        "sample": {
            **asdict(sample),
            "offset_seconds": sample.offset_seconds(),
            "round_trip_delay_seconds": sample.round_trip_delay_seconds(),
        },
        "distribution": asdict(distribution),
    }


@app.get("/api/clock-offset-distributions")
def clock_offset_distributions():
    return {
        "distributions": [
            asdict(distribution)
            for distribution in clock_offsets.distributions()
        ]
    }


@app.get("/api/clock-offset-distributions/{client_id}")
def clock_offset_distribution(client_id: str):
    try:
        distribution = clock_offsets.distribution_for(client_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return asdict(distribution)
