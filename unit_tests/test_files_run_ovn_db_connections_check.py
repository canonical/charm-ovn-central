# Copyright 2021 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from unittest import mock

from charms_openstack import test_utils

import run_ovn_db_connections_check as check


class TestRunOVNChecks(test_utils.PatchHelper):

    @mock.patch('run_ovn_db_connections_check.write_output_file')
    @mock.patch('run_ovn_db_connections_check.is_leader')
    def test_run_checks_not_leader(self, mock_leader, mock_write):
        mock_leader.return_value = False
        check.run_checks()
        mock_write.assert_called_once_with(
            "OK: no-op (unit is not the DB leader)"
        )

    @mock.patch('run_ovn_db_connections_check.write_output_file')
    @mock.patch('run_ovn_db_connections_check.check_output')
    @mock.patch('run_ovn_db_connections_check.parse_output')
    @mock.patch('run_ovn_db_connections_check.check_connections')
    @mock.patch('run_ovn_db_connections_check.aggregate_alerts')
    @mock.patch('run_ovn_db_connections_check.is_leader')
    def test_run_checks_leader(self, mock_leader, mock_aggregate, mock_parse,
                               mock_check, mock_check_output, mock_write):
        mock_leader.return_value = True
        mock_aggregate.return_value = "OK: fake status"
        check.run_checks()
        mock_write.assert_called_once_with("OK: fake status")

    def test_get_uuid(self):
        connection = {"_uuid": ["uuid", "fake-uuid"]}
        uuid = check.get_uuid(connection)
        self.assertEquals(uuid, "fake-uuid")

    def test_check_read_only_true(self):
        connection = {
            "_uuid": ["uuid", "fake-uuid"],
            "read_only": True,
        }
        alert = check.check_read_only(connection)
        self.assertEquals(alert.status, check.NAGIOS_STATUS_CRITICAL)

    def test_check_read_only_false(self):
        connection = {
            "_uuid": ["uuid", "fake-uuid"],
            "read_only": False,
        }
        alert = check.check_read_only(connection)
        self.assertEquals(alert.status, check.NAGIOS_STATUS_OK)

    def test_check_role_target_unexpected_role(self):
        connection = {
            "_uuid": ["uuid", "fake-uuid"],
            "role": "fakerole",
            "target": "pssl:6642",
        }
        alert = check.check_role_target(connection)
        self.assertEquals(alert.status, check.NAGIOS_STATUS_CRITICAL)

    def test_check_role_target_unexpected_target(self):
        connection = {
            "_uuid": ["uuid", "fake-uuid"],
            "role": "",
            "target": "pssl:26642",
        }
        alert = check.check_role_target(connection)
        self.assertEquals(alert.status, check.NAGIOS_STATUS_CRITICAL)

    def test_check_role_target_rbac_disabled(self):
        connection = {
            "_uuid": ["uuid", "fake-uuid"],
            "role": "",
            "target": "pssl:6642",
        }
        alert = check.check_role_target(connection)
        self.assertEquals(alert.status, check.NAGIOS_STATUS_WARNING)

    def test_check_role_target_ovn_controller_rbac(self):
        connection = {
            "_uuid": ["uuid", "fake-uuid"],
            "role": "ovn-controller",
            "target": "pssl:16642",
        }
        alert = check.check_role_target(connection)
        self.assertEquals(alert.status, check.NAGIOS_STATUS_CRITICAL)

    def test_check_role_target_ok_ovn_controller(self):
        connection = {
            "_uuid": ["uuid", "fake-uuid"],
            "role": "ovn-controller",
            "target": "pssl:6642",
        }
        alert = check.check_role_target(connection)
        self.assertEquals(alert.status, check.NAGIOS_STATUS_OK)

    def test_check_role_target_rbac_ok(self):
        connection = {
            "_uuid": ["uuid", "fake-uuid"],
            "role": "",
            "target": "pssl:16642",
        }
        alert = check.check_role_target(connection)
        self.assertEquals(alert.status, check.NAGIOS_STATUS_OK)

    def test_check_connections_too_many(self):
        connections = [
            {
                "_uuid": ["uuid", "fake-uuid-0"],
                "role": "",
                "target": "pssl:16642",
                "read_only": False,
            },
            {
                "_uuid": ["uuid", "fake-uuid-1"],
                "role": "",
                "target": "pssl:16642",
                "read_only": False,
            },
            {
                "_uuid": ["uuid", "fake-uuid-2"],
                "role": "ovn-controller",
                "target": "pssl:6642",
                "read_only": False,
            },
        ]
        alerts = check.check_connections(connections)
        self.assertIn(
            check.Alert(
                check.NAGIOS_STATUS_CRITICAL, "expected 2 connections, got 3"
            ),
            alerts,
        )

    def test_check_connections_too_many_controllers(self):
        connections = [
            {
                "_uuid": ["uuid", "fake-uuid-0"],
                "role": "",
                "target": "pssl:16642",
                "read_only": False,
            },
            {
                "_uuid": ["uuid", "fake-uuid-1"],
                "role": "ovn-controller",
                "target": "pssl:16642",
                "read_only": False,
            },
            {
                "_uuid": ["uuid", "fake-uuid-2"],
                "role": "ovn-controller",
                "target": "pssl:6642",
                "read_only": False,
            },
        ]
        exp1 = check.Alert(
            check.NAGIOS_STATUS_CRITICAL, "expected 2 connections, got 3"
        )
        exp2 = check.Alert(
            check.NAGIOS_STATUS_CRITICAL,
            "expected 1 ovn-controller connection, got 2",
        )
        alerts = check.check_connections(connections)
        self.assertIn(exp1, alerts)
        self.assertIn(exp2, alerts)

    def test_parse_output_correct(self):
        raw = '{"data":[[["uuid","fake-uuid-1"],["map",[]],60000,false,["set"'\
              ',[]],["map",[]],false,"ovn-controller",["map",[]],"pssl:6642"]'\
              ',[["uuid","fake-uuid-2"],["map",[]],60000,false,["set",[]],["m'\
              'ap",[]],false,"",["map",[]],"pssl:16642"]],"headings":["_uuid"'\
              ',"external_ids","inactivity_probe","is_connected","max_backoff'\
              '","other_config","read_only","role","status","target"]}'
        conns = check.parse_output(raw)
        self.assertEquals(len(conns), 2)

    def test_aggregate_alerts(self):
        alerts1 = [
            check.Alert(check.NAGIOS_STATUS_CRITICAL, "fakecrit"),
            check.Alert(check.NAGIOS_STATUS_WARNING, "fakewarn1"),
            check.Alert(check.NAGIOS_STATUS_WARNING, "fakewarn2"),
            check.Alert(check.NAGIOS_STATUS_OK, "fakeok"),
        ]
        filtered1 = check.aggregate_alerts(alerts1)
        self.assertEquals(
            filtered1,
            "CRITICAL: critical[1]: ['fakecrit']; "
            "warnings[2]: ['fakewarn1', 'fakewarn2']",
        )

        alerts2 = [
            check.Alert(check.NAGIOS_STATUS_WARNING, "fakewarn1"),
            check.Alert(check.NAGIOS_STATUS_WARNING, "fakewarn2"),
            check.Alert(check.NAGIOS_STATUS_OK, "fakeok"),
        ]
        filtered2 = check.aggregate_alerts(alerts2)
        self.assertEquals(
            filtered2, "WARNING: warnings[2]: ['fakewarn1', 'fakewarn2']"
        )

        alerts3 = [
            check.Alert(check.NAGIOS_STATUS_OK, "fakeok"),
        ]
        filtered3 = check.aggregate_alerts(alerts3)
        self.assertEquals(filtered3, "OK: OVN DB connections are normal")

    @mock.patch("run_ovn_db_connections_check.check_output")
    def test_is_leader_true(self, mock_check_output):
        mock_check_output.return_value = b"""0123
Name: OVN_Southbound
Cluster ID: 0123 (00000000-0000-0000-0000-000000000000)
Server ID: 4567 (00000000-0000-0000-0000-000000000001)
Address: ssl:a.b.c.d:6644
Status: cluster member
Role: leader
Term: 2
Leader: self
Vote: self
"""
        result = check.is_leader()
        self.assertTrue(result)

    @mock.patch("run_ovn_db_connections_check.check_output")
    def test_is_leader_follower(self, mock_check_output):
        mock_check_output.return_value = b"""0123
Name: OVN_Southbound
Cluster ID: 0123 (00000000-0000-0000-0000-000000000000)
Server ID: 4567 (00000000-0000-0000-0000-000000000001)
Address: ssl:a.b.c.d:6644
Status: cluster member
Role: follower
Term: 2
Leader: self
Vote: self
"""
        result = check.is_leader()
        self.assertFalse(result)

    @mock.patch("run_ovn_db_connections_check.check_output")
    def test_is_leader_no_role(self, mock_check_output):
        mock_check_output.return_value = b"""0123
Name: OVN_Southbound
Cluster ID: 0123 (00000000-0000-0000-0000-000000000000)
Server ID: 4567 (00000000-0000-0000-0000-000000000001)
Address: ssl:a.b.c.d:6644
Status: cluster member
Term: 2
Leader: self
Vote: self
"""
        result = check.is_leader()
        self.assertFalse(result)
