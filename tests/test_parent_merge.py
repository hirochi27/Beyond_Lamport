import unittest
from unittest.mock import patch

from app.beyond_lamport import OrderingProbabilityModel
from app.parallel_sequencer.models import OrderedRequestForParent, ParentUserOffsetDistribution
from app.parallel_sequencer.parent import merge_child_sequences


def request(message_id, child_id, timestamp_ns, *, offset_ns=0, variance_ns_squared=0):
    return OrderedRequestForParent(
        message_id=message_id,
        client_timestamp_ns=timestamp_ns,
        parent_user_offset=ParentUserOffsetDistribution(
            child_id=child_id,
            user_id=f"user-{child_id}",
            average_offset_seconds=offset_ns / 1e9,
            variance_offset_seconds_squared=variance_ns_squared / 1e18,
        ),
    )


class ParentMergeTests(unittest.TestCase):
    def test_two_children_use_parent_clock_times_and_standard_deviations(self):
        a1 = request("a1", "a", 10, offset_ns=100, variance_ns_squared=9)
        a2 = request("a2", "a", 30, offset_ns=100, variance_ns_squared=9)
        b1 = request("b1", "b", 115, offset_ns=-10, variance_ns_squared=16)
        b2 = request("b2", "b", 135, offset_ns=-10, variance_ns_squared=16)
        with patch.object(
            OrderingProbabilityModel,
            "probability_before",
            autospec=True,
            side_effect=OrderingProbabilityModel.probability_before,
        ) as compare:
            merged = merge_child_sequences([[a1, a2], [b1, b2]])

        self.assertEqual(merged, [b1, a1, b2, a2])
        first_comparison = {
            message.message_id: message.timestamp for message in compare.call_args_list[0].args[1:]
        }
        self.assertEqual(first_comparison["a1"].local_time, 110)
        self.assertAlmostEqual(first_comparison["a1"].uncertainty, 3)
        self.assertEqual(first_comparison["b1"].local_time, 105)
        self.assertAlmostEqual(first_comparison["b1"].uncertainty, 4)

    def test_three_children_preserve_input_order_within_each_child(self):
        a1, a2 = request("a1", "a", 40), request("a2", "a", 10)
        b1, b2 = request("b1", "b", 20), request("b2", "b", 50)
        c1, c2 = request("c1", "c", 30), request("c2", "c", 60)

        merged = merge_child_sequences([iter((a1, a2)), [], [b1, b2], [c1, c2]])

        self.assertEqual(merged, [b1, c1, a1, a2, b2, c2])

    def test_equal_parent_times_use_child_input_order(self):
        for variance in (0, 9):
            with self.subTest(variance=variance):
                first = request("z", "a", 100, offset_ns=10, variance_ns_squared=variance)
                second = request("a", "b", 110, variance_ns_squared=variance)
                self.assertEqual(merge_child_sequences([[first], [second]]), [first, second])
                self.assertEqual(merge_child_sequences([[second], [first]]), [second, first])

    def test_empty_and_single_child(self):
        single = [request("a1", "a", 20), request("a2", "a", 10)]
        self.assertEqual(merge_child_sequences([]), [])
        self.assertEqual(merge_child_sequences([[], []]), [])
        self.assertEqual(merge_child_sequences([[], single]), single)
