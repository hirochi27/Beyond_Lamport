# Benchmark data

Run the benchmark from the repository root, inside WSL:

```bash
.venv/bin/python -B -m app.benchmark_runner \
  --clients 5 10 25 50 --samples 100 --warmup 5 --interval 0.1 \
  --repeats 3 --probe-workers 5 --send-workers 5 --profile
```

No separately started server is required. The runner starts a private loopback
server on a free port and shuts it down afterward. It does not contact port 8000.
Each client sends exactly one request message. User counts and worker counts are
independent. The graph follows paper section 3.4: select one direction per pair, then
topologically sort without cycle repair. The two precedence-probability
calculations in the HTTP pipeline are retained.

## Files

Each invocation creates a new timestamped directory (Japan time, with a random
suffix) under this directory. Previous results are never overwritten.

- `metadata.json`: arguments, seed, Python/dependency versions, Git commit and
  dirty state, source hashes, timing definitions and completion/failure status.
- `source_snapshot/` and `working_tree.patch`: source at measurement time,
  including uncommitted Python files. A commit hash alone cannot identify dirty code.
- `metrics.csv`: seven stages for every client count and repetition.
- `summary.csv`: median, minimum and maximum exclusive time per stage/count.
- `trials.csv`: elapsed probe phase, sending phase, ordering endpoint and full
  measured pipeline, plus batch/removed-edge counts and ordering endpoint throughput.
- `n25-r1/` etc.: per-call `timings.csv`, metrics, probe samples, learned
  distributions, delta distributions, request messages, final batches and summary.
- `application.log`: existing application print output.
- `core-n25.prof` etc. (with `--profile`): a SEPARATE, single-thread cProfile
  replay after timing. Open with `snakeviz results/<run>/core-n25.prof`.

Raw results are Git-ignored; this guide, ignore rules, the runner and tests can be
versioned. Keep the WSL files as the working copy. Copy completed run directories
to a separate backed-up disk/cloud folder when needed; GitHub does not back up
these ignored measurements. For a report, explicitly add a selected small summary
with `git add -f`, rather than committing every log/profile/source snapshot.

## Stage definitions

| Stage | Measured scope |
| --- | --- |
| `probe` | Client HTTP GET /api/clock-probe through JSON decoding. Excludes warmup, sleep and sample-upload POST. |
| `offset_distribution` | Every distribution_for call during the measured pipeline, including per-sample updates and later recalculations. |
| `delta_distribution` | pairwise_delta_distributions. Nested distribution_for time is excluded from exclusive time. |
| `precedence_probability` | Full pair-list computation. The existing pipeline calls it twice, so totals include both. |
| `graph_creation` | Selected edge creation only. No greedy cycle repair is performed. |
| `topological_sort` | topological_sort_message_ids, including its adjacency and indegree preparation. |
| `batching` | Adjacent comparisons and batch construction; threshold strictly greater than 0.75. |

`inclusive_wall_s` includes nested instrumented function time;
`exclusive_wall_s` subtracts nested instrumented function time on the same thread.
The same distinction is available for thread CPU time. Function arguments,
HTTP/JSON serialization, message time conversion, logs and other uninstrumented
work outside these functions are not allocated to a computation stage.

Probe durations overlap across clients. Summed function times are NOT additive
end-to-end elapsed time; use trials.csv for elapsed latency/throughput.
The probe phase includes sample upload, distribution calculation and configured
sleep; the `probe` stage alone does not. Warmup is excluded from stage/phase
metrics but its clock-probe calls still warm up the actual server.

The source uses a seeded Gaussian RNG, but real wall-clock timestamps and HTTP
scheduling vary. Saved inputs are necessary for exact algorithm replay.
cProfile results are diagnostic replays and must not be used as unprofiled
throughput measurements. The replay includes distribution recomputation and one
ordering pass, not the original HTTP timeline. Every run is instrumented;
small function timings include timer/wrapper overhead.

The benchmark isolates server state, but client generation and server work share
one machine/process. Do not interpret this as multi-host network throughput.
Start with a small count: pair storage and graph construction still grow quadratically.

Runs with measurement_protocol_version=2 use the paper section 3.4 graph path.
Older runs without this field included a custom greedy cycle repair; their graph
times describe that older implementation and are not the paper-method baseline.
If a cyclic graph is supplied, the API returns HTTP 400 rather than deleting edges.
The paper assumes no probability ties; this implementation retains input pair
order at exactly 0.5 as an explicit deterministic convention. Same-batch messages
remain equally ranked.
