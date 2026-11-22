import sys
from pathlib import Path
from io import StringIO
import unittest
from unittest.mock import patch
from builtins import open as real_open

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pylib"))


def open_test_config(file, *args, **kwargs):
    if file == "/etc/digital-examen/computers.conf":
        return StringIO("")
    return real_open(file, *args, **kwargs)


with patch("builtins.open", side_effect=open_test_config):
    import de_mass_commands


class MassCommandTests(unittest.TestCase):
    def test_arbitrary_shell_command_runs_with_bounded_timeout(self):
        with patch.object(de_mass_commands.de_common, "run", return_value=(0, "ok")) as run:
            self.assertEqual(de_mass_commands.execute("printf ok", 1), (0, "ok"))
        run.assert_called_once_with(["/bin/sh", "-c", "printf ok"], 18 * 60)

    def test_empty_and_overlong_commands_are_rejected(self):
        for command in ("", " " * 3, "x" * 4001):
            with self.subTest(command_length=len(command)):
                self.assertEqual(de_mass_commands.execute(command, 1)[0], 2)


if __name__ == "__main__":
    unittest.main()
