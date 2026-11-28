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


class FrozenUpdateTests(unittest.TestCase):
    def function(self):
        import ast, os, signal, subprocess, time
        tree = ast.parse((ROOT / "bin/de-update").read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run")
        ns = dict(os=os, signal=signal, subprocess=subprocess, time=time, de_freeze=de_freeze, REPO_DIR="/tmp")
        exec(compile(ast.Module(body=[node], type_ignores=[]), "de-update", "exec"), ns)
        return ns["run"], ns

    def test_update_fetch_does_not_start_while_frozen(self):
        run, ns = self.function()
        with patch.object(de_freeze, "frozen", return_value=True), \
                patch.object(ns["subprocess"], "Popen") as start:
            self.assertEqual(run(["git", "fetch", "origin", "main"])[0], 125)
            start.assert_not_called()

    def test_freeze_cancels_the_whole_active_fetch_process_group(self):
        run, ns = self.function()
        with patch.object(de_freeze, "frozen", side_effect=[False, True]), \
                patch.object(ns["subprocess"], "Popen") as start, \
                patch.object(ns["os"], "killpg") as kill:
            start.return_value.pid = 424242
            start.return_value.communicate.return_value = (b"", None)
            self.assertEqual(run(["git", "fetch", "origin", "main"])[0], 125)
            kill.assert_called_once_with(424242, ns["signal"].SIGTERM)
            self.assertTrue(start.call_args.kwargs["start_new_session"])
