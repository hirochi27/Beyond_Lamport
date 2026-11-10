import unittest

from app.beyond_lamport import ClockOffsetEstimator, ClockProbeSample
from app.parallel_sequencer.child import compose_parent_user_offset


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
