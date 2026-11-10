import math
import random
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app.main as api
from app.beyond_lamport import (
    FairSequencer,
    Message,
    NoisyTimestamp,
    OrderingProbabilityModel,
)
from app.demo_client import DemoClient
from app.experiment_runner import print_topological_order


def message(name, timestamp, sigma=1.0):
    return Message(name, NoisyTimestamp(name, timestamp, sigma))


class GaussianProbabilityTests(unittest.TestCase):
    def test_known_normal_probability_and_reverse(self):
        model = OrderingProbabilityModel()
        first = message('a', 0.0)
        second = message('b', math.sqrt(2.0))
        self.assertAlmostEqual(model.probability_before(first, second), 0.8413447460685429)
        self.assertAlmostEqual(model.probability_before(second, first), 0.1586552539314571)

    def test_api_uses_sigma_independently_of_interval_multiplier(self):
        for multiplier in (1, 2, 5):
            with self.subTest(multiplier=multiplier):
                estimator = Mock()
                estimator.distribution_for.return_value = SimpleNamespace(
                    average_offset_seconds=0.0,
                    stddev_offset_seconds=1.0,
                    uncertainty_seconds=float(multiplier),
                )
                with patch.object(api, 'clock_offsets', estimator):
                    first = {'message_id': 'a', 'client_id': 'a', 'client_timestamp_ns': 0}
                    second = {'message_id': 'b', 'client_id': 'b', 'client_timestamp_ns': 1_000_000_000}
                    a = api.message_to_ordering_message(first)
                    b = api.message_to_ordering_message(second)
                self.assertEqual(a.timestamp.uncertainty, 1_000_000_000)
                self.assertEqual(first['uncertainty_ns'], multiplier * 1_000_000_000)
                self.assertAlmostEqual(
                    OrderingProbabilityModel().probability_before(a, b),
                    0.7602499389065233,
                )

    def test_subnanosecond_sigma_is_not_truncated(self):
        estimator = Mock()
        estimator.distribution_for.return_value = SimpleNamespace(
            average_offset_seconds=0.0, stddev_offset_seconds=0.5e-9, uncertainty_seconds=1e-9,
        )
        with patch.object(api, 'clock_offsets', estimator):
            converted = api.message_to_ordering_message(
                {'message_id': 'a', 'client_id': 'a', 'client_timestamp_ns': 0}
            )
        self.assertAlmostEqual(converted.timestamp.uncertainty, 0.5)


class BatchTests(unittest.TestCase):
    def test_empty_and_single_message(self):
        sequencer = FairSequencer()
        self.assertEqual(sequencer.sequence([]), [])
        item = message('a', 0)
        self.assertEqual(sequencer.sequence([item]), [[item]])

    def test_only_adjacent_probabilities_define_boundaries(self):
        # A->C exceeds 75%, but A->B and B->C do not: one batch.
        items = [message('a', 0), message('b', 0.5), message('c', 1.0)]
        sequencer = FairSequencer()
        self.assertGreater(sequencer.model.probability_before(items[0], items[2]), 0.75)
        self.assertEqual(sequencer.sequence(list(reversed(items))), [items])

    def test_high_confidence_separates_batches(self):
        items = [message('a', 0), message('b', 1), message('c', 1.5)]
        self.assertEqual(FairSequencer().sequence(items), [[items[0]], items[1:]])

    def test_threshold_is_strict_and_configurable(self):
        items = [message('a', 0), message('b', 1)]
        for threshold in (0.75, 0.9):
            model = Mock(confidence_threshold=threshold)
            sequencer = FairSequencer(model)
            model.probability_before.return_value = threshold
            self.assertEqual(sequencer.batch_ordered_messages(items), [items])
            model.probability_before.return_value = threshold + 1e-8
            self.assertEqual(sequencer.batch_ordered_messages(items), [[items[0]], [items[1]]])

    def test_api_returns_shared_ranks_and_persists_batch_output(self):
        estimator = Mock()
        estimator.distribution_for.return_value = SimpleNamespace(
            average_offset_seconds=0.0, stddev_offset_seconds=1.0, uncertainty_seconds=2.0,
        )
        stored = [
            {'message_id': name, 'client_id': name, 'client_timestamp_ns': timestamp}
            for name, timestamp in [('c', 1_500_000_000), ('a', 0), ('b', 1_000_000_000)]
        ]
        with (
            patch.object(api, 'clock_offsets', estimator),
            patch.object(api, 'request_messages', stored),
            patch.object(api, 'pairwise_precedence_probabilities', []),
            patch.object(api, 'topological_ordering', None),
        ):
            result = api.calculate_and_store_topological_order()
            self.assertEqual(result['batch_threshold'], 0.75)
            self.assertEqual(result['ordered_message_ids'], ['a', 'b', 'c'])
            self.assertEqual(
                [(batch['rank'], batch['message_ids']) for batch in result['batches']],
                [(0, ['a']), (1, ['b', 'c'])],
            )
            self.assertEqual(
                [[item['message_id'] for item in batch['messages']] for batch in result['batches']],
                [['a'], ['b', 'c']],
            )
            self.assertEqual(api.get_topological_order(), result)
            with patch('builtins.print') as output:
                print_topological_order(result)
            output.assert_any_call('rank=1: b, c')

    def test_api_empty_input_returns_no_batches(self):
        with (
            patch.object(api, 'request_messages', []),
            patch.object(api, 'pairwise_precedence_probabilities', []),
            patch.object(api, 'topological_ordering', None),
        ):
            self.assertEqual(api.calculate_and_store_topological_order()['batches'], [])


class MessageNoiseTests(unittest.TestCase):
    def test_one_noise_sample_is_added_to_recorded_true_time(self):
        client = DemoClient('http://unused', 'a')
        with (
            patch('app.demo_client.time.time_ns', return_value=1000) as clock,
            patch.object(client, 'sample_clock_offset_ns', return_value=-30) as sample,
        ):
            result = client.build_ticket_create_message('example')
        self.assertEqual(result['payload']['true_created_time_ns'], 1000)
        self.assertEqual(result['client_timestamp_ns'], 970)
        clock.assert_called_once_with()
        sample.assert_called_once_with()

    def test_seeded_gaussian_noise_is_sampled_for_every_message(self):
        client = DemoClient('http://unused', 'a', 50, 40, random_seed=42)
        expected_rng = random.Random(42)
        expected = [round(expected_rng.gauss(50, 40)) for _ in range(10)]
        with patch('app.demo_client.time.time_ns', return_value=1000):
            observed = [
                client.build_ticket_create_message('example')['client_timestamp_ns'] - 1000
                for _ in expected
            ]
        self.assertEqual(observed, expected)
        self.assertGreater(len(set(observed)), 1)

    def test_zero_sigma_keeps_constant_offset(self):
        client = DemoClient('http://unused', 'a', -50, 0, random_seed=42)
        with patch('app.demo_client.time.time_ns', return_value=1000):
            result = client.build_ticket_create_message('example')
        self.assertEqual(result['client_timestamp_ns'], 950)


if __name__ == '__main__':
    unittest.main()
