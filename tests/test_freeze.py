import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_raw_inspection import ROOT, de_common
import de_freeze
import de_tabs


class FreezeTests(unittest.TestCase):
    def test_suspend_resume_and_reboot_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(de_freeze, "FLAG", Path(directory) / "frozen"), \
                patch.object(de_freeze, "_applied", None), \
                patch.object(de_freeze.subprocess, "run") as run:
            run.return_value.returncode = 0
            de_freeze.apply(True)
            self.assertTrue(de_freeze.frozen())
            self.assertIn("stop", run.call_args.args[0])
            self.assertNotIn("de-agent.service", run.call_args.args[0])
            de_freeze.apply(True)
            self.assertEqual(run.call_count, 1)
            de_freeze._applied = None
            de_freeze.apply(True)
            self.assertEqual(run.call_count, 2)
            de_freeze.apply(False)
            self.assertFalse(de_freeze.frozen())
            self.assertIn("start", run.call_args.args[0])
            self.assertIn("de-screen.service", run.call_args.args[0])
            self.assertIn("de-net.service", run.call_args.args[0])

    def test_failed_systemd_request_is_retried(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(de_freeze, "FLAG", Path(directory) / "frozen"), \
                patch.object(de_freeze, "_applied", None), \
                patch.object(de_freeze.subprocess, "run") as run:
            run.return_value.returncode = 1
            de_freeze.apply(True)
            de_freeze.apply(True)
            self.assertEqual(run.call_count, 2)
            self.assertTrue(de_freeze.frozen())

    def test_tabs_are_not_collected_when_frozen(self):
        with patch.object(de_freeze, "frozen", return_value=True), \
                patch.object(de_tabs.de_lesson_activity, "record") as record:
            answer = de_tabs.process(1000, {"profile": "a" * 32, "tabs": [], "lesson_requests": {}})
            self.assertFalse(answer["tracking"])
            self.assertIsNone(answer["rules"])
            record.assert_not_called()

    def test_autostart_cannot_resume_suspended_services(self):
        for unit in de_freeze.UNITS:
            with self.subTest(unit=unit):
                self.assertIn("ConditionPathExists=!/var/lib/digital-examen/frozen",
                              (ROOT / "systemd" / unit).read_text())
