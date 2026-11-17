import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pylib'))
import de_keys


class KeyDiscoveryTests(unittest.TestCase):
    def test_nested_download_and_invalid_file(self):
        with tempfile.TemporaryDirectory() as folder:
            nested = Path(folder) / 'Downloads' / 'keys'
            nested.mkdir(parents=True)
            key = nested / 'DE-KEY-owner (2).TXT'
            key.write_text('\ufefftoken=a.b.c\n', encoding='utf-8')
            (nested / 'de-key.txt').write_bytes(b'\xff')
            (nested / 'de-key-link.txt').symlink_to(key)
            self.assertEqual(de_keys.find_in(folder, 'de-key.txt'), str(key))
            self.assertEqual(de_keys.read_token(key), 'a.b.c')
            self.assertFalse(de_keys.name_matches('other-de-key.txt', 'de-key.txt'))

    def test_external_device_inheritance_and_removable(self):
        tree = {'blockdevices': [
            {'type': 'disk', 'tran': 'usb', 'children': [
                {'children': [{'path': '/dev/sdb1', 'fstype': 'vfat'}]}]},
            {'type': 'disk', 'rm': True, 'path': '/dev/mmcblk0', 'fstype': 'exfat'},
            {'type': 'disk', 'hotplug': 1, 'path': '/dev/sdc', 'fstype': 'ntfs'},
            {'type': 'disk', 'path': '/dev/sda', 'fstype': 'ext4'}]}
        with patch.object(de_keys.subprocess, 'check_output', return_value=json.dumps(tree).encode()):
            self.assertEqual([p['path'] for p in de_keys.usb_partitions()], ['/dev/sdb1', '/dev/mmcblk0', '/dev/sdc'])

    def test_mount_failure_does_not_hide_next_device(self):
        parts = [{'path': '/dev/sdb1'}, {'path': '/dev/sdc1'}]
        with patch.object(de_keys, 'usb_partitions', return_value=parts), \
                patch.object(de_keys.subprocess, 'call', side_effect=[1, 0]) as mount, \
                patch.object(de_keys.subprocess, 'run') as unmount, \
                patch.object(de_keys, 'find_in', return_value='/tmp/key'), \
                patch.object(de_keys, 'save_key', return_value=de_keys.SAVED_KEY):
            self.assertEqual(de_keys.find_on_usb('de-key.txt'), de_keys.SAVED_KEY)
            self.assertEqual(mount.call_count, 2)
            unmount.assert_called_once()


if __name__ == '__main__':
    unittest.main()
