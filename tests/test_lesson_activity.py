import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import de_lesson_activity as activity
import de_tabs


class LessonActivityTest(unittest.TestCase):
    def test_merges_input_and_preserves_counters_after_restart(self):
        start = 1200
        with tempfile.TemporaryDirectory() as directory, patch.object(activity, "STATE", Path(directory) / "state"):
            activity.configure(start)
            with patch.object(activity, "active", True):
                activity.record(stamp=start + 20)
                activity.record(stamp=start + 31)
            de_tabs.activate((os.getuid(), "test"), [], [])
            with patch.object(de_tabs.time, "time", return_value=start + 58):
                de_tabs.process(os.getuid(), {
                    "profile": "a" * 32,
                    "lesson_requests": {str(start): 7},
                })
                de_tabs.process(os.getuid() + 1, {
                    "profile": "b" * 32,
                    "lesson_requests": {str(start): 99},
                })
            report = activity.snapshot(start + 63)
            self.assertEqual(report[0]["seconds"], 40)
            activity.acknowledged(report)
            self.assertIsNone(activity.snapshot(start + 64))
            activity.configure(None)
            activity.configure(start)
            self.assertEqual(activity.snapshot(start + 123)[0], {
                "start": start, "seconds": 41, "requests": 7, "privileged": False,
            })
            activity.configure(start + 2700)
            self.assertEqual(activity.snapshot(start + 2703)[0]["seconds"], 0)
            activity.configure(None)
