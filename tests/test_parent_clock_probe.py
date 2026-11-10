import unittest
from unittest.mock import call, patch

from app import probe_client
from app.parallel_sequencer.child import measure_parent_clock_offset


class ParentClockProbeTests(unittest.TestCase):
    def test_probe_counts_warmup_and_parent_minus_child_offset(self):
        url = "http://parent/api/clock-probe"
        with (
            patch.object(probe_client, "get_json") as get_json,
            patch.object(probe_client.time, "time_ns", side_effect=[0, 20, 100, 120]),
            patch.object(probe_client.time, "sleep") as sleep,
        ):
            get_json.side_effect = [
                {},
                {"sequencer_receive_time_ns": 60, "sequencer_send_time_ns": 60},
                {"sequencer_receive_time_ns": 170, "sequencer_send_time_ns": 170},
            ]
            distribution = measure_parent_clock_offset(
                "http://parent/",
                "child-a",
                sample_count=2,
                warmup_count=1,
                interval=0.25,
            )

        self.assertEqual(get_json.call_args_list, [call(url)] * 3)
        self.assertEqual(sleep.call_args_list, [call(0.25)] * 2)
        self.assertEqual(distribution.client_id, "child-a")
        self.assertEqual(distribution.sample_count, 2)
        self.assertEqual(distribution.accepted_sample_count, 2)
        self.assertAlmostEqual(distribution.average_offset_seconds * 1e9, 55)
        self.assertAlmostEqual(distribution.stddev_offset_seconds * 1e9, 50 ** 0.5)

    def test_each_measurement_starts_with_a_fresh_distribution(self):
        with (
            patch.object(probe_client, "get_json") as get_json,
            patch.object(probe_client.time, "time_ns", side_effect=[0, 20, 100, 120]),
        ):
            get_json.side_effect = [
                {"sequencer_receive_time_ns": 60, "sequencer_send_time_ns": 60},
                {"sequencer_receive_time_ns": 60, "sequencer_send_time_ns": 60},
            ]
            offsets = [
                measure_parent_clock_offset(
                    "http://parent", "child-a", sample_count=1, warmup_count=0, interval=0,
                )
                for _ in range(2)
            ]

        self.assertEqual([item.sample_count for item in offsets], [1, 1])
        self.assertEqual([item.stddev_offset_seconds for item in offsets], [0, 0])
        self.assertAlmostEqual(offsets[0].average_offset_seconds * 1e9, 50)
        self.assertAlmostEqual(offsets[1].average_offset_seconds * 1e9, -50)
