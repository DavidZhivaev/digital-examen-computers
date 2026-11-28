import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import de_chromium_lock


class ChromiumLockTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.profile = self.home / '.config/chromium'
        self.profile.mkdir(parents=True)
        self.lock = self.profile / 'SingletonLock'
        self.lock.symlink_to('previous-school-host-999999')
        self.address = str(self.profile / 'SingletonSocket')

    def test_dead_socket_does_not_block_browser_after_rename(self):
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(self.address)
        listener.close()
        self.assertTrue(Path(self.address).is_socket())
        with patch.object(os, 'kill', side_effect=ProcessLookupError):
            de_chromium_lock.clear_stale_lock(self.home)
        self.assertFalse(self.lock.is_symlink())

    def test_live_listener_keeps_profile_locked(self):
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(self.address)
            listener.listen(1)
            with patch.object(os, 'kill', side_effect=ProcessLookupError):
                de_chromium_lock.clear_stale_lock(self.home)
        self.assertTrue(self.lock.is_symlink())

    def test_live_process_keeps_profile_locked(self):
        with patch.object(os, 'kill', return_value=None):
            de_chromium_lock.clear_stale_lock(self.home)
        self.assertTrue(self.lock.is_symlink())
