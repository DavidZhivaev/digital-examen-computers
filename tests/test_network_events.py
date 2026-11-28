import unittest
from unittest.mock import patch

from test_raw_inspection import de_common
import de_network_events as network


class NetworkIdentityTests(unittest.TestCase):
    def connection(self, kind, name):
        def nmcli(*args):
            if args[:2] == ("-t", "-f"):
                return "uuid:" + kind + ":eth0:/activation/1"
            field = args[1]
            return {
                "GENERAL.DEFAULT,GENERAL.DEFAULT6": "yes\nno", "connection.id": "profile",
                "802-11-wireless.ssid": name, "802-1x.identity": "",
                "IP4.ADDRESS,IP6.ADDRESS": "192.168.1.5/24\nfe80::1/64",
            }[field]
        return nmcli

    def test_wifi_unicode_and_cached_address(self):
        with patch.object(network, "STATE_DATA", {}), \
                patch.object(network, "nmcli", side_effect=self.connection("802-11-wireless", "Школа № 1580")) as query:
            selected = network.selected_connection()
            self.assertEqual(selected[2], "Школа № 1580")
            self.assertEqual(selected[5], "192.168.1.5")
            network.STATE_DATA["key"] = [network.BOOT, *selected]
            query.reset_mock()
            self.assertEqual(network.selected_connection(), selected)
            self.assertNotIn(unittest.mock.call("-g", "IP4.ADDRESS,IP6.ADDRESS", "device", "show", "eth0"), query.call_args_list)

    def test_ethernet_is_identified_as_wired(self):
        with patch.object(network, "STATE_DATA", {}), \
                patch.object(network, "nmcli", side_effect=self.connection("802-3-ethernet", "")):
            self.assertEqual(network.selected_connection()[2], "Проводное соединение")
