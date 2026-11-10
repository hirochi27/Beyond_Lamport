import random
import unittest
from unittest.mock import patch

from fastapi import HTTPException
import app.main as api
from app.beyond_lamport import (
    FairSequencer,
    Message,
    NoisyTimestamp,
    PairwisePrecedenceProbability,
    build_candidate_precedence_edges,
    build_pairwise_precedence_probabilities,
    build_topological_ordering,
)


def item(name, time, sigma=1.0):
    return Message(name, NoisyTimestamp(name, time, sigma))


class PaperOrderingTests(unittest.TestCase):
    def test_every_selected_edge_is_kept_without_confidence_reordering(self):
        messages = [item('c', 2), item('a', 0), item('b', 1)]
        probabilities = build_pairwise_precedence_probabilities(messages)
        expected_edges = build_candidate_precedence_edges(probabilities)
        ordering = build_topological_ordering(messages, probabilities)
        self.assertEqual(ordering.ordered_message_ids, ['a', 'b', 'c'])
        self.assertEqual(ordering.kept_edges, expected_edges)
        self.assertEqual(len(ordering.kept_edges), 3)
        self.assertEqual(ordering.removed_edges, [])

    def test_heterogeneous_gaussian_graph_matches_corrected_time_order(self):
        for seed in range(5):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                messages = [
                    item(str(index), rng.uniform(-10, 10), rng.uniform(0.1, 5))
                    for index in range(25)
                ]
                probabilities = build_pairwise_precedence_probabilities(messages)
                result = build_topological_ordering(messages, probabilities)
                self.assertEqual(
                    result.ordered_message_ids,
                    [message.message_id for message in sorted(
                        messages, key=lambda message: message.timestamp.local_time,
                    )],
                )
                ranks = {message_id: rank for rank, message_id in enumerate(result.ordered_message_ids)}
                self.assertTrue(all(
                    ranks[edge.from_message_id] < ranks[edge.to_message_id]
                    for edge in result.kept_edges
                ))
                self.assertEqual(len(result.kept_edges), 300)
                self.assertEqual(result.removed_edges, [])

    def test_tied_probabilities_use_explicit_input_order_and_same_batch(self):
        messages = [item('c', 0), item('a', 0), item('b', 0)]
        probabilities = build_pairwise_precedence_probabilities(messages)
        self.assertTrue(all(pair.probability_first_before_second == 0.5 for pair in probabilities))
        ordering = build_topological_ordering(messages, probabilities)
        self.assertEqual(ordering.ordered_message_ids, ['c', 'a', 'b'])
        self.assertEqual(FairSequencer().sequence(messages), [messages])

    def test_cyclic_input_is_rejected_instead_of_repaired(self):
        messages = [item('a', 0), item('b', 1), item('c', 2)]
        probabilities = [
            PairwisePrecedenceProbability('a', 'b', 0.9),
            PairwisePrecedenceProbability('b', 'c', 0.8),
            PairwisePrecedenceProbability('a', 'c', 0.1),
        ]
        with self.assertRaisesRegex(ValueError, 'cyclic inputs are unsupported'):
            build_topological_ordering(messages, probabilities)
        self.assertEqual(len(probabilities), 3)

    def test_api_reports_unsupported_cycle_without_reordering_stored_messages(self):
        stored = [{'message_id': name} for name in ('a', 'b', 'c')]
        messages = [item(name, index) for index, name in enumerate(('a', 'b', 'c'))]
        probabilities = [
            PairwisePrecedenceProbability('a', 'b', 0.9),
            PairwisePrecedenceProbability('b', 'c', 0.8),
            PairwisePrecedenceProbability('a', 'c', 0.1),
        ]
        with (
            patch.object(api, 'request_messages', stored),
            patch.object(api, 'topological_ordering', None),
            patch.object(api, 'pairwise_precedence_probabilities', []),
            patch.object(api, 'message_to_ordering_message', side_effect=messages),
            patch.object(api, 'build_pairwise_precedence_probabilities', return_value=probabilities),
        ):
            with self.assertRaises(HTTPException) as raised:
                api.calculate_and_store_topological_order()
            self.assertEqual(raised.exception.status_code, 400)
            self.assertIn('cyclic inputs are unsupported', raised.exception.detail)
            self.assertEqual([message['message_id'] for message in stored], ['a', 'b', 'c'])
            self.assertIsNone(api.topological_ordering)


if __name__ == '__main__':
    unittest.main()
