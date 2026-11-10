import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

from app import benchmark_runner as bench


class StageRecorderTests(unittest.TestCase):
    def test_nested_compute_is_not_double_counted_in_exclusive_time(self):
        recorder = bench.StageRecorder()
        recorder.enabled = True
        with (
            patch.object(bench.time, 'perf_counter_ns', side_effect=[0, 10, 40, 100]),
            patch.object(bench.time, 'thread_time_ns', side_effect=[0, 5, 15, 50]),
        ):
            with recorder.measure('delta_distribution', 'outer'):
                with recorder.measure('offset_distribution', 'inner'):
                    pass
        metrics = {row['stage']: row for row in recorder.metrics()}
        self.assertAlmostEqual(metrics['delta_distribution']['inclusive_wall_s'], 100e-9)
        self.assertAlmostEqual(metrics['delta_distribution']['exclusive_wall_s'], 70e-9)
        self.assertAlmostEqual(metrics['offset_distribution']['exclusive_wall_s'], 30e-9)
        self.assertAlmostEqual(metrics['delta_distribution']['exclusive_thread_cpu_s'], 40e-9)

    def test_disabled_warmup_is_not_recorded(self):
        recorder = bench.StageRecorder()
        with recorder.measure('probe', 'warmup'):
            pass
        self.assertEqual(recorder.events, [])

    def test_exception_is_recorded_and_stack_is_unwound(self):
        recorder = bench.StageRecorder()
        recorder.enabled = True
        with self.assertRaises(ValueError):
            with recorder.measure('graph_creation', 'failure'):
                raise ValueError('test')
        self.assertEqual(len(recorder.events), 1)
        self.assertEqual(recorder.local.stack, [])

    def test_summary_compares_repetitions_not_different_counts(self):
        rows = [
            {'clients': clients, 'stage': 'batching', 'calls': 1, 'exclusive_wall_s': value}
            for clients, value in [(5, 1.0), (5, 3.0), (10, 8.0)]
        ]
        result = {row['clients']: row for row in bench.summarize(rows)}
        self.assertEqual(result[5]['median_exclusive_wall_s'], 2.0)
        self.assertEqual(result[5]['min_exclusive_wall_s'], 1.0)
        self.assertEqual(result[5]['max_exclusive_wall_s'], 3.0)
        self.assertEqual(result[10]['repeats'], 1)

    def test_small_http_trial_has_all_stages_and_one_message_per_client(self):
        args = SimpleNamespace(
            samples=3, warmup=1, interval=0, probe_workers=2, send_workers=2,
            jitter_ms=40, seed=42, timeout=10,
        )
        original_distribution = bench.core.ClockOffsetEstimator.distribution_for
        with (
            tempfile.TemporaryDirectory() as directory,
            redirect_stdout(io.StringIO()),
            patch.object(bench.api, 'clock_offsets', bench.core.ClockOffsetEstimator()),
            patch.object(bench.api, 'request_messages', []),
            patch.object(bench.api, 'pairwise_precedence_probabilities', []),
            patch.object(bench.api, 'topological_ordering', None),
            bench.private_server() as base_url,
        ):
            trial_dir = Path(directory) / 'trial'
            rows, summary = bench.run_trial(base_url, args, 2, 1, trial_dir)
            self.assertEqual({row['stage'] for row in rows}, set(bench.STAGES))
            metrics = {row['stage']: row for row in rows}
            self.assertEqual(metrics['probe']['calls'], 6)
            self.assertEqual(metrics['offset_distribution']['calls'], 14)
            self.assertEqual(metrics['precedence_probability']['calls'], 2)
            self.assertEqual(metrics['graph_creation']['calls'], 1)
            self.assertEqual(summary['messages'], 2)
            self.assertEqual(summary['removed_edges'], 0)
            self.assertTrue((trial_dir / 'inputs.json').is_file())
            self.assertTrue((trial_dir / 'timings.csv').is_file())
            self.assertIs(bench.core.ClockOffsetEstimator.distribution_for, original_distribution)


if __name__ == '__main__':
    unittest.main()
