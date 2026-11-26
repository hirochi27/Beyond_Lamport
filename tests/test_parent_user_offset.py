import unittest

from app.beyond_lamport import ClockOffsetEstimator, ClockProbeSample
from app.parallel_sequencer.child import compose_parent_user_offset, order_child_requests
from app.parallel_sequencer.models import ChildRequest


def measure_offsets(client_id, offsets_ns):
    estimator = ClockOffsetEstimator(sigma_multiplier=7.0)
    for offset_ns in offsets_ns:
        estimator.add_sample(ClockProbeSample(
            client_id=client_id,
            client_send_time_ns=0,
            sequencer_receive_time_ns=20 + offset_ns,
            sequencer_send_time_ns=20 + offset_ns,
            client_receive_time_ns=40,
        ))
    return estimator.distribution_for(client_id)


class ParentUserOffsetTests(unittest.TestCase):
    def test_composition_adds_signed_means_and_raw_variances(self):
        parent_child = measure_offsets("child-a", [8, 12])
        child_user = measure_offsets("user-a", [-8, -2])

        combined = compose_parent_user_offset(parent_child, child_user)

        self.assertEqual(combined.child_id, "child-a")
        self.assertEqual(combined.user_id, "user-a")
        self.assertAlmostEqual(combined.average_offset_seconds * 1e9, 5)
        self.assertAlmostEqual(combined.variance_offset_seconds_squared * 1e18, 26)

    def test_zero_variance_is_preserved_when_both_links_are_constant(self):
        parent_child = measure_offsets("child-a", [-5])
        child_user = measure_offsets("user-b", [3])

        combined = compose_parent_user_offset(parent_child, child_user)

        self.assertAlmostEqual(combined.average_offset_seconds * 1e9, -2)
        self.assertEqual(combined.variance_offset_seconds_squared, 0.0)


class ChildOrderingTests(unittest.TestCase):
    def setUp(self):
        self.parent_child = measure_offsets("child-a", [18, 22])
        self.child_user_offsets = {
            "user-a": measure_offsets("user-a", [8, 12]),
            "user-b": measure_offsets("user-b", [-12, -8]),
        }

    def test_output_preserves_child_order_and_raw_timestamps_with_parent_offsets(self):
        requests = [
            ChildRequest("last", "user-a", 120),
            ChildRequest("first", "user-b", 115),
            ChildRequest("middle", "user-a", 105),
        ]

        first, middle, last = order_child_requests(
            requests, self.parent_child, self.child_user_offsets,
        )

        self.assertEqual([item.message_id for item in (first, middle, last)], [
            "first", "middle", "last",
        ])
        self.assertEqual([item.client_timestamp_ns for item in (first, middle, last)], [
            115, 105, 120,
        ])
        self.assertEqual(first.parent_user_offset.child_id, "child-a")
        self.assertEqual(first.parent_user_offset.user_id, "user-b")
        self.assertAlmostEqual(first.parent_user_offset.average_offset_seconds * 1e9, 10)
        self.assertAlmostEqual(first.parent_user_offset.variance_offset_seconds_squared * 1e18, 16)
        self.assertEqual(middle.parent_user_offset.user_id, "user-a")
        self.assertAlmostEqual(middle.parent_user_offset.average_offset_seconds * 1e9, 30)
        self.assertIs(middle.parent_user_offset, last.parent_user_offset)

    def test_empty_input_produces_an_empty_sequence(self):
        self.assertEqual(order_child_requests([], self.parent_child, {}), [])

    def test_rejects_duplicate_request_ids(self):
        requests = [
            ChildRequest("same-id", "user-a", 100),
            ChildRequest("same-id", "user-b", 120),
        ]
        with self.assertRaisesRegex(ValueError, "duplicate request ID: same-id"):
            order_child_requests(requests, self.parent_child, self.child_user_offsets)

    def test_rejects_missing_user_offset(self):
        with self.assertRaisesRegex(ValueError, "no child-user offset for user: unknown"):
            order_child_requests(
                [ChildRequest("request", "unknown", 100)],
                self.parent_child,
                self.child_user_offsets,
            )

    def test_rejects_offset_for_a_different_user(self):
        with self.assertRaisesRegex(ValueError, "offset belongs to a different user: user-a"):
            order_child_requests(
                [ChildRequest("request", "user-a", 100)],
                self.parent_child,
                {"user-a": self.child_user_offsets["user-b"]},
            )
