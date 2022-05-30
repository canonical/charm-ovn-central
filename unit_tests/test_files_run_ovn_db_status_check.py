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

import textwrap

from unittest import mock

from charms_openstack import test_utils

import run_ovn_db_status_check as check


class TestRunOVNChecks(test_utils.PatchHelper):
    SOUTHBOUND = "sb"
    NORTHBOUND = "nb"

    @mock.patch('run_ovn_db_status_check.write_output_file')
    @mock.patch('run_ovn_db_status_check.check_output')
    @mock.patch('run_ovn_db_status_check.parse_output')
    @mock.patch('run_ovn_db_status_check.aggregate_alerts')
    def test_run_checks_sb(self, mock_aggregate, mock_parse,
                           mock_check_output, mock_write):
        mock_aggregate.return_value = "OK: fake status"
        check.run_checks(self.SOUTHBOUND)
        mock_write.assert_called_once_with(
            "OK: fake status",
            "/var/lib/nagios/ovn_sb_db_status.out"
        )

    @mock.patch('run_ovn_db_status_check.write_output_file')
    @mock.patch('run_ovn_db_status_check.check_output')
    @mock.patch('run_ovn_db_status_check.parse_output')
    @mock.patch('run_ovn_db_status_check.aggregate_alerts')
    def test_run_checks_nb(self, mock_aggregate, mock_parse,
                           mock_check_output, mock_write):
        mock_aggregate.return_value = "OK: fake status"
        check.run_checks(self.NORTHBOUND)
        mock_write.assert_called_once_with(
            "OK: fake status",
            "/var/lib/nagios/ovn_nb_db_status.out"
        )

    def test_get_db_status_sb_warning(self):
        status = {
            "Name": "OVN_Southbound",
            "Status": "joining cluster",
            "Server ID": "fake id",
        }
        alert1 = check.get_db_status(status, self.SOUTHBOUND)
        self.assertEquals(alert1.status, check.NAGIOS_STATUS_WARNING)

        status = {
            "Name": "OVN_Southbound",
            "Status": "leaving cluster",
            "Server ID": "fake id",
        }
        alert2 = check.get_db_status(status, self.SOUTHBOUND)
        self.assertEquals(alert2.status, check.NAGIOS_STATUS_WARNING)

        status = {
            "Name": "OVN_Southbound",
            "Status": "left cluster",
            "Server ID": "fake id",
        }
        alert3 = check.get_db_status(status, self.SOUTHBOUND)
        self.assertEquals(alert3.status, check.NAGIOS_STATUS_WARNING)

    def test_get_db_status_nb_warning(self):
        status = {
            "Name": "OVN_Northbound",
            "Status": "joining cluster",
            "Server ID": "fake id",
        }
        alert1 = check.get_db_status(status, self.NORTHBOUND)
        self.assertEquals(alert1.status, check.NAGIOS_STATUS_WARNING)

        status = {
            "Name": "OVN_Northbound",
            "Status": "leaving cluster",
            "Server ID": "fake id",
        }
        alert2 = check.get_db_status(status, self.NORTHBOUND)
        self.assertEquals(alert2.status, check.NAGIOS_STATUS_WARNING)

        status = {
            "Name": "OVN_Northbound",
            "Status": "left cluster",
            "Server ID": "fake id",
        }
        alert3 = check.get_db_status(status, self.NORTHBOUND)
        self.assertEquals(alert3.status, check.NAGIOS_STATUS_WARNING)

    def test_get_db_status_sb_critical(self):
        status = {
            "Name": "OVN_Southbound",
            "Status": "failed",
            "Server ID": "fake id",
        }
        alert1 = check.get_db_status(status, self.SOUTHBOUND)
        self.assertEquals(alert1.status, check.NAGIOS_STATUS_CRITICAL)

        status = {
            "Name": "OVN_Southbound",
            "Status": "disconnected from the cluster (election timeout)",
            "Server ID": "fake id",
        }
        alert2 = check.get_db_status(status, self.SOUTHBOUND)
        self.assertEquals(alert2.status, check.NAGIOS_STATUS_CRITICAL)

    def test_get_db_status_nb_critical(self):
        status = {
            "Name": "OVN_Northbound",
            "Status": "failed",
            "Server ID": "fake id",
        }
        alert1 = check.get_db_status(status, self.NORTHBOUND)
        self.assertEquals(alert1.status, check.NAGIOS_STATUS_CRITICAL)

        status = {
            "Name": "OVN_Northbound",
            "Status": "disconnected from the cluster (election timeout)",
            "Server ID": "fake id",
        }
        alert2 = check.get_db_status(status, self.NORTHBOUND)
        self.assertEquals(alert2.status, check.NAGIOS_STATUS_CRITICAL)

    def test_get_db_status_sb_ok(self):
        status = {
            "Name": "OVN_Southbound",
            "Status": "cluster member",
            "Server ID": "fake id",
        }
        alert1 = check.get_db_status(status, self.SOUTHBOUND)
        self.assertEquals(alert1.status, check.NAGIOS_STATUS_OK)

    def test_get_db_status_nb_ok(self):
        status = {
            "Name": "OVN_Northbound",
            "Status": "cluster member",
            "Server ID": "fake id",
        }
        alert1 = check.get_db_status(status, self.NORTHBOUND)
        self.assertEquals(alert1.status, check.NAGIOS_STATUS_OK)

    def test_parse_output_correct(self):
        raw = textwrap.dedent(
            """\
            e8c5
            Name: OVN_Northbound
            Cluster ID: 6a8f (6a8f9149-3368-4bae-88c5-d6fe2be9b847)
            Server ID: e8c5 (e8c5232f-864c-4e61-990d-e54c666be4bc)
            Address: ssl:10.5.0.24:6643
            Status: cluster member
            Role: leader
            Term: 25
            Leader: self
            Vote: self

            Election timer: 1000
            Log: [2, 29]
            Entries not yet committed: 0
            Entries not yet applied: 0
            Connections: ->f4d0 ->70dc <-f4d0 <-70dc
            Servers:
                f4d0 (f4d0 at ssl:10.5.0.4:6643) next_index=29 match_index=28
                70dc (70dc at ssl:10.5.0.20:6643) next_index=29 match_index=28
            """
        )
        formatted_output = check.parse_output(raw)
        self.assertEquals(formatted_output["Name"], "OVN_Northbound")
        self.assertEquals(formatted_output["Server ID"],
                          "e8c5 (e8c5232f-864c-4e61-990d-e54c666be4bc)")
        self.assertEquals(formatted_output["Status"], "cluster member")
        self.assertEquals(formatted_output["Role"], "leader")

    def test_aggregate_alerts_sb(self):
        formatted_output = {
            "Name": "OVN_Southbound",
            "Role": "leader",
            "Server ID": "fake id",
        }
        alerts1 = [
            check.Alert(check.NAGIOS_STATUS_CRITICAL, "fakecrit"),
            check.Alert(check.NAGIOS_STATUS_WARNING, "fakewarn1"),
            check.Alert(check.NAGIOS_STATUS_WARNING, "fakewarn2"),
            check.Alert(check.NAGIOS_STATUS_OK, "fakeok"),
        ]
        filtered1 = check.aggregate_alerts(alerts1, formatted_output)
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
        filtered2 = check.aggregate_alerts(alerts2, formatted_output)
        self.assertEquals(
            filtered2, "WARNING: warnings[2]: ['fakewarn1', 'fakewarn2']"
        )

        alerts3 = [
            check.Alert(check.NAGIOS_STATUS_OK, "fakeok"),
        ]

        filtered3 = check.aggregate_alerts(alerts3, formatted_output)
        self.assertEquals(
            filtered3, "OK: OVN_Southbound DB leader fake id status is normal"
        )

    def test_aggregate_alerts_nb(self):
        formatted_output = {
            "Name": "OVN_Northbound",
            "Role": "leader",
            "Server ID": "fake id",
        }
        alerts1 = [
            check.Alert(check.NAGIOS_STATUS_CRITICAL, "fakecrit"),
            check.Alert(check.NAGIOS_STATUS_WARNING, "fakewarn1"),
            check.Alert(check.NAGIOS_STATUS_WARNING, "fakewarn2"),
            check.Alert(check.NAGIOS_STATUS_OK, "fakeok"),
        ]
        filtered1 = check.aggregate_alerts(alerts1, formatted_output)
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
        filtered2 = check.aggregate_alerts(alerts2, formatted_output)
        self.assertEquals(
            filtered2, "WARNING: warnings[2]: ['fakewarn1', 'fakewarn2']"
        )

        alerts3 = [
            check.Alert(check.NAGIOS_STATUS_OK, "fakeok"),
        ]

        filtered3 = check.aggregate_alerts(alerts3, formatted_output)
        self.assertEquals(
            filtered3, "OK: OVN_Northbound DB leader fake id status is normal"
        )
