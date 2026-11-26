import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_raw_inspection import ROOT, de_common, de_inspection, relay

import ast
import json
import os
from types import SimpleNamespace

source = ast.parse((ROOT / "bin/de-agent").read_text())
function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == "save_policy")
namespace = {"json": json, "os": os, "de_common": de_common, "POLICY_FILE": "", "log": lambda message: None}
exec(compile(ast.Module(body=[function], type_ignores=[]), "de-agent", "exec"), namespace)
agent = SimpleNamespace(save_policy=namespace["save_policy"])


class SchoolPolicyTests(unittest.TestCase):
    def test_only_confirmed_groups_take_ownership_until_reboot(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run"
            with patch.object(de_inspection, "RUN", run), \
                    patch.object(de_common, "RUN_DIR", str(run)), \
                    patch.dict(namespace, {"POLICY_FILE": str(Path(directory) / "policy.json")}):
                self.assertFalse(relay.network_owned())
                agent.save_policy({"groups": []})
                self.assertFalse(relay.network_owned())
                agent.save_policy({"groups": [{"name": "group"}]})
                self.assertTrue(relay.network_owned())
                agent.save_policy({"groups": []})
                self.assertTrue(relay.network_owned())
                (run / "network-owned").unlink()
                agent.save_policy({"groups": []})
                self.assertFalse(relay.network_owned())

    def test_unchanged_server_policy_reacquires_ownership_after_reboot(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = {"groups": [{"name": "group"}]}
            with patch.object(de_common, "RUN_DIR", directory), \
                    patch.object(de_common, "read_file", return_value=json.dumps(policy, sort_keys=True)), \
                    patch.object(de_common, "write_file") as write:
                agent.save_policy(policy)
                write.assert_called_once_with(str(Path(directory) / "network-owned"), "1", 0o600)

    def test_fresh_boot_keeps_school_routes_and_proxy(self):
        with patch.object(relay, "network_owned", return_value=False), \
                patch.object(relay, "relay_exception") as exceptions, \
                patch.object(relay, "relay_route") as route, \
                patch.object(relay.de_ansible_network, "reconcile") as proxy, \
                patch.object(relay.time, "sleep", side_effect=InterruptedError):
            with self.assertRaises(InterruptedError):
                relay.reconcile_loop()
            exceptions.assert_called_once_with(False)
            route.assert_not_called()
            proxy.assert_not_called()

    def test_empty_policy_does_not_install_direct_user_routes(self):
        with patch.object(de_inspection.groups, "get", return_value=[]), \
                patch.object(relay, "network_owned", return_value=False), \
                patch.object(relay, "PEOPLE", set()), \
                patch.object(relay, "direct_routes", return_value=True) as direct:
            self.assertTrue(relay.update_people())
            direct.assert_called_once_with(set())
