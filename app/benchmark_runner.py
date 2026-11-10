"""Measure the existing HTTP experiment in an isolated local server.

Run: .venv/bin/python -m app.benchmark_runner --clients 5 10 25 50
Timing and cProfile replay are separate; normal server code is not modified.
"""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager, redirect_stdout
import cProfile
import csv
from datetime import datetime, timedelta, timezone
from functools import wraps
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import random
import socket
import statistics
import subprocess
import sys
import threading
import time
from unittest.mock import patch
from urllib import request
import uuid

import uvicorn

from app import beyond_lamport as core
from app import main as api
from app.demo_client import DemoClient

ROOT = Path(__file__).resolve().parents[1]
STAGES = (
    'probe', 'offset_distribution', 'delta_distribution',
    'precedence_probability', 'graph_creation', 'topological_sort', 'batching',
)
METRIC_FIELDS = (
    'calls', 'inclusive_wall_s', 'exclusive_wall_s',
    'thread_cpu_s', 'exclusive_thread_cpu_s',
)


class StageRecorder:
    """Record nested timings per thread; overlapping threads remain separate."""

    def __init__(self):
        self.enabled = False
        self.events = []
        self.local = threading.local()
        self.lock = threading.Lock()

    @contextmanager
    def measure(self, stage, detail):
        if not self.enabled:
            yield
            return
        if not hasattr(self.local, 'stack'):
            self.local.stack = []
        stack = self.local.stack
        frame = {'child_wall_ns': 0, 'child_cpu_ns': 0}
        stack.append(frame)
        cpu_start = time.thread_time_ns()
        wall_start = time.perf_counter_ns()
        try:
            yield
        finally:
            wall_ns = time.perf_counter_ns() - wall_start
            cpu_ns = time.thread_time_ns() - cpu_start
            stack.pop()
            if stack:
                stack[-1]['child_wall_ns'] += wall_ns
                stack[-1]['child_cpu_ns'] += cpu_ns
            event = {
                'stage': stage, 'detail': detail,
                'thread_id': threading.get_ident(), 'start_ns': wall_start,
                'inclusive_wall_s': wall_ns / 1e9,
                'exclusive_wall_s': max(0, wall_ns - frame['child_wall_ns']) / 1e9,
                'thread_cpu_s': cpu_ns / 1e9,
                'exclusive_thread_cpu_s': max(0, cpu_ns - frame['child_cpu_ns']) / 1e9,
            }
            with self.lock:
                self.events.append(event)

    def wrap(self, stage, function):
        @wraps(function)
        def measured(*args, **kwargs):
            with self.measure(stage, function.__name__):
                return function(*args, **kwargs)
        return measured

    def metrics(self):
        rows = {stage: {'stage': stage, **dict.fromkeys(METRIC_FIELDS, 0)} for stage in STAGES}
        for event in self.events:
            row = rows[event['stage']]
            row['calls'] += 1
            for field in METRIC_FIELDS[1:]:
                row[field] += event[field]
        return list(rows.values())


@contextmanager
def instrument(recorder):
    """Wrap compute functions without changing production algorithms."""
    with ExitStack() as stack:
        targets = (
            (core.ClockOffsetEstimator, 'distribution_for', 'offset_distribution'),
            (core.ClockOffsetEstimator, 'pairwise_delta_distributions', 'delta_distribution'),
            (api, 'build_pairwise_precedence_probabilities', 'precedence_probability'),
            (core, 'build_candidate_precedence_edges', 'graph_creation'),
            (core, 'topological_sort_message_ids', 'topological_sort'),
            (core.FairSequencer, 'batch_ordered_messages', 'batching'),
        )
        for owner, name, stage in targets:
            stack.enter_context(patch.object(owner, name, recorder.wrap(stage, getattr(owner, name))))
        original_get = DemoClient.get_json

        @wraps(original_get)
        def measured_get(client, path):
            if path == '/api/clock-probe':
                with recorder.measure('probe', 'clock_probe_http_get'):
                    return original_get(client, path)
            return original_get(client, path)

        stack.enter_context(patch.object(DemoClient, 'get_json', measured_get))
        yield


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def write_csv(path, rows):
    if not rows:
        return
    with path.open('w', newline='', encoding='utf-8') as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def post_json(base_url, path, payload, timeout):
    body = json.dumps(payload).encode('utf-8')
    outgoing = request.Request(
        base_url + path, data=body,
        headers={'Content-Type': 'application/json'}, method='POST',
    )
    with request.urlopen(outgoing, timeout=timeout) as response:
        return json.load(response)


@contextmanager
def private_server():
    """An ephemeral port and independent process state protect the usual server."""
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        api.app, host='127.0.0.1', port=port,
        log_level='warning', access_log=False,
    ))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError('Private benchmark server failed to start')
            time.sleep(0.01)
        yield 'http://127.0.0.1:' + str(port)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=2)
        sock.close()


def git_output(*args):
    result = subprocess.run(
        ['git', *args], cwd=ROOT, capture_output=True,
        text=True, encoding='utf-8', errors='replace', timeout=15,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def save_provenance(run_dir, args):
    hashes = {}
    for directory in ('app', 'tests'):
        for path in sorted((ROOT / directory).glob('*.py')):
            relative = path.relative_to(ROOT)
            content = path.read_bytes()
            destination = run_dir / 'source_snapshot' / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            hashes[str(relative)] = hashlib.sha256(content).hexdigest()
    requirements = ROOT / 'requirements.txt'
    if requirements.exists():
        (run_dir / 'requirements.txt').write_bytes(requirements.read_bytes())
    (run_dir / 'working_tree.patch').write_text(git_output('diff', 'HEAD') or '', encoding='utf-8')
    metadata = {
        'created_at': datetime.now(timezone(timedelta(hours=9))).isoformat(),
        'status': 'running',
        'command': [sys.executable, *sys.argv],
        'config': {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()},
        'git_commit': git_output('rev-parse', 'HEAD'),
        'git_branch': git_output('branch', '--show-current'),
        'git_status': git_output('status', '--short'),
        'source_sha256': hashes,
        'python': sys.version,
        'platform': platform.platform(),
        'logical_cpu_count': os.cpu_count(),
        'packages': dict(sorted(
            (distribution.metadata['Name'], distribution.version)
            for distribution in importlib.metadata.distributions()
        )),
        'protocol': {
            'mode': 'existing_HTTP_pipeline_in_private_loopback_server',
            'measurement_protocol_version': 2,
            'graph_algorithm': 'paper_section_3_4_full_selected_graph_no_cycle_repair',
            'messages_per_client': 1,
            'batch_threshold': 0.75,
            'probe_workers': args.probe_workers,
            'send_workers': args.send_workers,
            'warmup_in_stage_metrics': False,
            'precedence_calls_per_trial': 2,
            'profile_in_stage_metrics': False,
            'probe_definition': 'client GET /api/clock-probe through decoded JSON; POST sample upload excluded',
            'cpu_definition': 'thread CPU time, not process-wide CPU time',
            'exclusive_definition': 'inclusive minus nested instrumented functions on the same thread',
            'summation_warning': 'probe durations overlap between clients; their sum is not end-to-end elapsed time',
            'seed_warning': 'noise RNG is seeded; wall-clock timestamps and HTTP scheduling are not deterministic',
        },
    }
    write_json(run_dir / 'metadata.json', metadata)
    return metadata


def replay_core(estimator, messages):
    """Replay saved model input without HTTP; cProfile runs only on this thread."""
    estimator.distributions()
    estimator.pairwise_delta_distributions()
    probabilities = core.build_pairwise_precedence_probabilities(messages)
    ordering = core.build_topological_ordering(messages, probabilities)
    by_id = {message.message_id: message for message in messages}
    return core.FairSequencer().batch_ordered_messages([
        by_id[message_id] for message_id in ordering.ordered_message_ids
    ])


def run_trial(base_url, args, client_count, repeat, trial_dir):
    trial_dir.mkdir()
    recorder = StageRecorder()
    # This module runs in a dedicated benchmark process, never the usual server.
    api.request_messages.clear()
    api.pairwise_precedence_probabilities.clear()
    api.topological_ordering = None
    post_json(base_url, '/api/experiment/start', {
        'client_count': client_count, 'samples_per_client': args.samples,
        'sigma_multiplier': 2.0,
    }, args.timeout)
    offset_rng = random.Random(args.seed)
    offsets = [round(offset_rng.uniform(-170, 130) * 1e6) for _ in range(client_count)]
    clients = [
        DemoClient(
            base_url, 'client-' + str(index), offsets[index],
            round(args.jitter_ms * 1e6), args.seed + repeat * 1_000_000 + index,
        )
        for index in range(client_count)
    ]
    probe_workers = min(args.probe_workers, client_count)
    send_workers = min(args.send_workers, client_count)
    with instrument(recorder):
        def warmup(client):
            client.run_warmup(args.warmup, args.interval)

        with ThreadPoolExecutor(max_workers=probe_workers) as executor:
            list(executor.map(warmup, clients))
        recorder.enabled = True
        pipeline_start = time.perf_counter()
        phase_start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=probe_workers) as executor:
            list(executor.map(
                lambda client: client.collect_samples(args.samples, args.interval), clients,
            ))
        probe_phase_wall_s = time.perf_counter() - phase_start
        frozen = post_json(base_url, '/api/experiment/freeze', {}, args.timeout)
        phase_start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=send_workers) as executor:
            list(executor.map(
                lambda client: client.send_request_message('ticket-' + client.client_id), clients,
            ))
        send_phase_wall_s = time.perf_counter() - phase_start
        post_json(base_url, '/api/request-messages/estimate-times', {}, args.timeout)
        post_json(base_url, '/api/request-messages/precedence-probabilities', {}, args.timeout)
        ordering_start = time.perf_counter()
        result = post_json(base_url, '/api/request-messages/topological-order', {}, args.timeout)
        ordering_endpoint_wall_s = time.perf_counter() - ordering_start
        pipeline_wall_s = time.perf_counter() - pipeline_start
        recorder.enabled = False

    expected_ids = {message['message_id'] for message in api.request_messages}
    batch_ids = [message_id for batch in result['batches'] for message_id in batch['message_ids']]
    assert len(batch_ids) == client_count and set(batch_ids) == expected_ids
    assert len({message['client_id'] for message in api.request_messages}) == client_count
    assert [batch['rank'] for batch in result['batches']] == list(range(len(result['batches'])))
    rows = [
        {'clients': client_count, 'repeat': repeat, **row}
        for row in recorder.metrics()
    ]
    metrics = {row['stage']: row for row in rows}
    assert metrics['probe']['calls'] == client_count * args.samples
    assert metrics['precedence_probability']['calls'] == 2
    assert metrics['graph_creation']['calls'] == 1
    assert metrics['topological_sort']['calls'] == 1
    assert metrics['batching']['calls'] == 1
    summary = {
        'clients': client_count, 'repeat': repeat,
        'messages': client_count, 'message_pairs': client_count * (client_count - 1) // 2,
        'probe_phase_wall_s': probe_phase_wall_s,
        'send_phase_wall_s': send_phase_wall_s,
        'ordering_endpoint_wall_s': ordering_endpoint_wall_s,
        'ordering_endpoint_messages_per_s': client_count / ordering_endpoint_wall_s,
        'pipeline_wall_s': pipeline_wall_s,
        'batches': len(result['batches']),
        'removed_edges': len(result['removed_edges']),
    }
    # Keep exact inputs/outputs so the nondeterministic network trial is auditable.
    write_json(trial_dir / 'inputs.json', {
        'clock_offset_means_ns': offsets,
        'jitter_stddev_ns': round(args.jitter_ms * 1e6),
        'probe_samples': {
            client_id: [vars(sample) for sample in samples]
            for client_id, samples in api.clock_offsets._samples_by_client.items()
        },
        'offset_distributions': [vars(item) for item in api.clock_offsets.distributions()],
        'messages': api.request_messages,
    })
    write_json(trial_dir / 'delta_distributions.json', frozen['delta_distributions'])
    write_json(trial_dir / 'ordering.json', result)
    write_json(trial_dir / 'summary.json', summary)
    write_csv(trial_dir / 'timings.csv', recorder.events)
    write_csv(trial_dir / 'metrics.csv', rows)
    return rows, summary


def summarize(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row['clients'], row['stage'])].append(row)
    summaries = []
    for (clients, stage), values in grouped.items():
        wall = [row['exclusive_wall_s'] for row in values]
        summaries.append({
            'clients': clients, 'stage': stage, 'repeats': len(values),
            'median_exclusive_wall_s': statistics.median(wall),
            'min_exclusive_wall_s': min(wall),
            'max_exclusive_wall_s': max(wall),
            'median_calls': statistics.median(row['calls'] for row in values),
        })
    return summaries


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--clients', nargs='+', type=int, default=[5, 10, 25, 50])
    parser.add_argument('--samples', type=int, default=100)
    parser.add_argument('--warmup', type=int, default=5)
    parser.add_argument('--interval', type=float, default=0.1)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--probe-workers', type=int, default=5)
    parser.add_argument('--send-workers', type=int, default=5)
    parser.add_argument('--jitter-ms', type=float, default=40.0)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--timeout', type=float, default=120.0)
    parser.add_argument('--profile', action='store_true', help='Separate cProfile replay after timing')
    parser.add_argument('--output-root', type=Path, default=ROOT / 'results')
    args = parser.parse_args()
    if any(value <= 0 for value in [*args.clients, args.samples, args.repeats, args.probe_workers, args.send_workers]):
        parser.error('clients, samples, repeats and worker counts must be positive')
    if args.warmup < 0 or args.interval < 0 or args.jitter_ms < 0 or args.timeout <= 0:
        parser.error('invalid warmup, interval, jitter or timeout')
    if len(set(args.clients)) != len(args.clients):
        parser.error('client counts must be unique')
    return args


def main():
    args = parse_args()
    stamp = datetime.now(timezone(timedelta(hours=9))).strftime('%Y%m%dT%H%M%S+0900')
    run_dir = args.output_root.resolve() / (stamp + '-' + uuid.uuid4().hex[:8])
    run_dir.mkdir(parents=True)
    metadata = save_provenance(run_dir, args)
    print('Saving benchmark to ' + str(run_dir), flush=True)
    all_rows = []
    all_trials = []
    try:
        with (run_dir / 'application.log').open('w', encoding='utf-8') as log:
            with redirect_stdout(log), private_server() as base_url:
                for client_count in args.clients:
                    for repeat in range(args.repeats):
                        trial_dir = run_dir / ('n' + str(client_count) + '-r' + str(repeat + 1))
                        rows, trial = run_trial(base_url, args, client_count, repeat + 1, trial_dir)
                        all_rows.extend(rows)
                        all_trials.append(trial)
                        write_csv(run_dir / 'metrics.csv', all_rows)
                        write_csv(run_dir / 'trials.csv', all_trials)
                        write_csv(run_dir / 'summary.csv', summarize(all_rows))
                    if args.profile:
                        # No timer wrappers or HTTP wait loops in the profile replay.
                        messages = [api.message_to_ordering_message(item) for item in api.request_messages]
                        profiler = cProfile.Profile()
                        profiler.runcall(replay_core, api.clock_offsets, messages)
                        profiler.dump_stats(str(run_dir / ('core-n' + str(client_count) + '.prof')))
        metadata['status'] = 'complete'
        metadata['completed_trials'] = len(all_trials)
        write_json(run_dir / 'metadata.json', metadata)
    except BaseException as error:
        metadata['status'] = 'failed'
        metadata['error'] = repr(error)
        metadata['completed_trials'] = len(all_trials)
        write_json(run_dir / 'metadata.json', metadata)
        raise
    for row in summarize(all_rows):
        print('n={clients} {stage}: {median_exclusive_wall_s:.6f}s (median)'.format(**row))
    print('Saved ' + str(len(all_trials)) + ' trials to ' + str(run_dir))


if __name__ == '__main__':
    main()
