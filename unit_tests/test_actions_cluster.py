# Copyright 2022 Canonical Ltd
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

from collections import namedtuple
from copy import deepcopy
from unittest import TestCase
from unittest.mock import MagicMock, patch, call
from uuid import uuid4

import yaml

import actions.cluster as cluster_actions

# Placeholder for charmhelpers.contrib.network.ovs.ovn.OVNClusterStatus
OvnClusterStatusMock = namedtuple(
    "OvnClusterStatusMock",
    [
        "name",
        "cluster_id",
        "server_id",
        "address",
        "status",
        "role",
        "term",
        "leader",
        "vote",
        "election_timer",
        "log",
        "connections",
        "entries_not_yet_committed",
        "entries_not_yet_applied",
        "servers",
    ],
)


class ClusterActionTests(TestCase):

    UNIT_MAPPING = {
        "ovn-central/0": {"id": "aa11", "address": "ssl:10.0.0.1:6644"},
        "ovn-central/1": {"id": "bb22", "address": "ssl:10.0.0.2:6644"},
        "ovn-central/2": {"id": "cc33", "address": "ssl:10.0.0.3:6644"},
    }

    def setUp(self):
        """Setup and clean up frequent mocks."""
        super().setUp()
        mocks = [
            patch.object(cluster_actions.hookenv, "function_get"),
            patch.object(cluster_actions.hookenv, "function_set"),
            patch.object(cluster_actions.hookenv, "function_fail"),
            patch.object(cluster_actions, "ovn_appctl"),
        ]

        for mock in mocks:
            mock.start()
            self.addCleanup(mock.stop)

        # Mock actions mapped in the cluster.py otherwise they'd refer
        # to non-mocked functions.
        self.mapped_action_kick_server = MagicMock()
        self.mapped_action_cluster_status = MagicMock()
        cluster_actions.ACTIONS[
            "cluster-kick"
        ] = self.mapped_action_kick_server
        cluster_actions.ACTIONS[
            "cluster-status"
        ] = self.mapped_action_cluster_status

    def get_cluster_status_sample(self):
        """Get sample OVNStatus object."""
        address = "ssl:%s:6644" % self.UNIT_MAPPING["ovn-central/0"]["address"]
        servers = []
        for data in self.UNIT_MAPPING.values():
            servers.append((data["id"], data["address"]))

        return OvnClusterStatusMock(
            name="ovsdb",
            cluster_id=uuid4(),
            server_id=uuid4(),
            address=address,
            status="cluster member",
            role="leader",
            term=1,
            leader="self",
            vote="self",
            election_timer=1,
            log="[1, 1]",
            entries_not_yet_committed=0,
            entries_not_yet_applied=0,
            connections="",
            servers=servers,
        )

    def test_format_cluster_status(self):
        """Test turning OVNClusterStatus into dict.

        Resulting dict also contains additional info mapping cluster servers
        to the juju units.
        """
        sample_status = self.get_cluster_status_sample()
        expected_servers = {}
        unit_ip_map = {}
        for unit, data in self.UNIT_MAPPING.items():
            _, ip_addr, _ = data["address"].split(":")
            unit_ip_map[unit] = ip_addr
            expected_servers[data["id"]] = {
                "Address": data["address"],
                "Unit": unit,
            }

        cluster_status = cluster_actions._format_cluster_status(
            sample_status, unit_ip_map
        )
        # Compare resulting dict with expected data
        self.assertEquals(
            str(sample_status.cluster_id), cluster_status["Cluster ID"]
        )
        self.assertEquals(
            str(sample_status.server_id), cluster_status["Server ID"]
        )
        self.assertEquals(sample_status.address, cluster_status["Address"])
        self.assertEquals(sample_status.status, cluster_status["Status"])
        self.assertEquals(sample_status.role, cluster_status["Role"])
        self.assertEquals(sample_status.term, cluster_status["Term"])
        self.assertEquals(sample_status.leader, cluster_status["Leader"])
        self.assertEquals(sample_status.vote, cluster_status["Vote"])
        self.assertEquals(sample_status.log, cluster_status["Log"])
        self.assertEquals(
            sample_status.entries_not_yet_committed,
            cluster_status["Entries not yet committed"],
        )
        self.assertEquals(
            sample_status.entries_not_yet_applied,
            cluster_status["Entries not yet applied"],
        )
        self.assertEquals(expected_servers, cluster_status["Servers"])

    def test_format_cluster_status_missing_server(self):
        """Test turning OVNClusterStatus into dict with a missing server.

        This use-case happens when OVN cluster reports server that does not run
        on active ovn-central unit. For example, if server ran on unit that was
        destroyed and did not leave cluster gracefully. in such case, resulting
        status shows "Unit" attribute of this server as "UNKNOWN"
        """
        sample_status = self.get_cluster_status_sample()
        missing_unit = list(self.UNIT_MAPPING.keys())[-1]
        missing_server = ""
        unit_ip_map = {}

        for unit, data in self.UNIT_MAPPING.items():
            _, ip_addr, _ = data["address"].split(":")
            if unit == missing_unit:
                missing_server = data["id"]
                continue

            unit_ip_map[unit] = ip_addr

        cluster_status = cluster_actions._format_cluster_status(
            sample_status, unit_ip_map
        )

        self.assertEquals(
            cluster_status["Servers"][missing_server]["Unit"], "UNKNOWN"
        )

    def test_format_cluster_parsing_failure(self):
        """Test failure to parse status with format_cluster_status()."""
        sample_status = self.get_cluster_status_sample()
        garbled_ip = "987dajSA"
        sample_status.servers.append(("ffff", garbled_ip))

        unit_ip_map = {}

        for unit, data in self.UNIT_MAPPING.items():
            _, ip_addr, _ = data["address"].split(":")
            unit_ip_map[unit] = ip_addr

        with self.assertRaises(cluster_actions.StatusParsingException):
            cluster_actions._format_cluster_status(sample_status, unit_ip_map)

    @patch.object(cluster_actions.reactive, "endpoint_from_flag")
    @patch.object(cluster_actions.hookenv, "local_unit")
    def test_cluster_ip_map(self, mock_local_unit, mock_endpoint_from_flag):
        """Test generating map of unit IDs and their IPs."""
        expected_map = {}
        remote_unit_data = deepcopy(self.UNIT_MAPPING)
        remote_units = []
        local_unit_name = "ovn-central/0"
        local_unit_data = remote_unit_data.pop(local_unit_name)
        for unit_name, data in remote_unit_data.items():
            _, ip, _ = data["address"].split(":")
            unit = MagicMock()
            unit.unit_name = unit_name
            unit.received = {"bound-address": ip}
            remote_units.append(unit)
            expected_map[unit_name] = ip

        _, local_unit_ip, _ = local_unit_data["address"].split(":")
        expected_map[local_unit_name] = local_unit_ip

        endpoint = MagicMock()
        relation = MagicMock()

        relation.units = remote_units
        endpoint.relations = [relation]
        endpoint.cluster_local_addr = local_unit_ip

        mock_local_unit.return_value = local_unit_name
        mock_endpoint_from_flag.return_value = endpoint

        unit_mapping = cluster_actions._cluster_ip_map()

        self.assertEquals(unit_mapping, expected_map)

    @patch.object(
        cluster_actions.charms_openstack.charm, "provide_charm_instance"
    )
    @patch.object(cluster_actions, "_cluster_ip_map")
    @patch.object(cluster_actions, "_format_cluster_status")
    def test_cluster_status(
        self, format_cluster_mock, cluster_map_mock, provide_instance_mock
    ):
        """Test cluster-status action implementation."""
        sb_raw_status = "Southbound status"
        nb_raw_status = "Northbound status"
        charm_instance = MagicMock()
        charm_instance.cluster_status.side_effect = [
            sb_raw_status,
            nb_raw_status,
        ]
        provide_instance_mock.return_value = charm_instance

        ip_map = {"ovn-central/0": "10.0.0.0"}
        cluster_map_mock.return_value = ip_map

        sb_cluster_status = {"Southbound": "status"}
        nb_cluster_status = {"Northbound": "status"}
        format_cluster_mock.side_effect = [
            sb_cluster_status,
            nb_cluster_status
        ]

        # Test successfully generating cluster status
        cluster_actions.cluster_status()

        expected_calls = [
            call(
                {
                    "southbound-cluster": yaml.dump(
                        sb_cluster_status, sort_keys=False
                    )
                }
            ),
            call(
                {
                    "norhtbound-cluster": yaml.dump(
                        nb_cluster_status, sort_keys=False
                    )
                }
            ),
        ]
        cluster_actions.hookenv.function_set.has_calls(expected_calls)
        cluster_actions.hookenv.function_fail.asser_not_called()

        # Reset mocks
        cluster_actions.hookenv.function_set.reset_mock()

        # Test failure to generate cluster status
        msg = "parsing failed"
        format_cluster_mock.side_effect = (
            cluster_actions.StatusParsingException(msg)
        )

        cluster_actions.cluster_status()

        cluster_actions.hookenv.function_set.assert_not_called()
        cluster_actions.hookenv.function_fail.assert_called_once_with(msg)

    def test_cluster_kick_no_server(self):
        """Test running cluster-kick action without providing any server ID."""
        cluster_actions.hookenv.function_get.return_value = ""
        err = "At least one server ID to kick must be specified."

        cluster_actions.kick_server()

        cluster_actions.hookenv.function_fail.assert_called_once_with(err)
        cluster_actions.hookenv.function_set.assert_not_called()
        cluster_actions.ovn_appctl.assert_not_called()

    def test_cluster_kick_sb_server(self):
        """Test kicking single Southbound server from cluster."""
        sb_id = "11aa"
        nb_id = ""
        expected_msg = {"southbound": "requested kick of {}".format(sb_id)}
        kick_command = ("cluster/kick", "OVN_Southbound", sb_id)

        # Test successfully kicking server from Southbound cluster
        cluster_actions.hookenv.function_get.side_effect = [sb_id, nb_id]

        cluster_actions.kick_server()

        cluster_actions.hookenv.function_fail.assert_not_called()
        cluster_actions.hookenv.function_set.assert_called_once_with(
            expected_msg
        )
        cluster_actions.ovn_appctl.assert_called_once_with(
            "ovnsb_db", kick_command
        )

        # Reset mocks
        cluster_actions.hookenv.function_set.reset_mock()
        cluster_actions.hookenv.function_fail.reset_mock()
        cluster_actions.ovn_appctl.reset_mock()
        cluster_actions.hookenv.function_get.side_effect = [sb_id, nb_id]

        # Test failure to kick server from Southbound cluster
        process_output = "Failed to kick server"
        exception = cluster_actions.CalledProcessError(
            -1, "/usr/sbin/ovs-appctl", process_output
        )
        cluster_actions.ovn_appctl.side_effect = exception
        err = "Failed to kick Southbound cluster member {}: {}".format(
            sb_id, process_output
        )

        cluster_actions.kick_server()

        cluster_actions.hookenv.function_set.assert_not_called()
        cluster_actions.hookenv.function_fail.assert_called_once_with(err)
        cluster_actions.ovn_appctl.assert_called_once_with(
            "ovnsb_db", kick_command
        )

    def test_cluster_kick_nb_server(self):
        """Test kicking single Northbound server from cluster."""
        sb_id = ""
        nb_id = "22bb"
        expected_msg = {"northbound": "requested kick of {}".format(nb_id)}
        kick_command = ("cluster/kick", "OVN_Northbound", nb_id)

        # Test successfully kicking server from Northbound cluster
        cluster_actions.hookenv.function_get.side_effect = [sb_id, nb_id]

        cluster_actions.kick_server()

        cluster_actions.hookenv.function_fail.assert_not_called()
        cluster_actions.hookenv.function_set.assert_called_once_with(
            expected_msg
        )
        cluster_actions.ovn_appctl.assert_called_once_with(
            "ovnnb_db", kick_command
        )

        # Reset mocks
        cluster_actions.hookenv.function_set.reset_mock()
        cluster_actions.hookenv.function_fail.reset_mock()
        cluster_actions.ovn_appctl.reset_mock()
        cluster_actions.hookenv.function_get.side_effect = [sb_id, nb_id]

        # Test failure to kick server from Northbound cluster
        process_output = "Failed to kick server"
        exception = cluster_actions.CalledProcessError(
            -1, "/usr/sbin/ovs-appctl", process_output
        )
        cluster_actions.ovn_appctl.side_effect = exception
        err = "Failed to kick Northbound cluster member {}: {}".format(
            nb_id, process_output
        )

        cluster_actions.kick_server()

        cluster_actions.hookenv.function_set.assert_not_called()
        cluster_actions.hookenv.function_fail.assert_called_once_with(err)
        cluster_actions.ovn_appctl.assert_called_once_with(
            "ovnnb_db", kick_command
        )

    def test_cluster_kick_both_server(self):
        """Test kicking Southbound and Northbound servers from cluster."""
        sb_id = "11bb"
        nb_id = "22bb"
        expected_func_set_calls = [
            call({"southbound": "requested kick of {}".format(sb_id)}),
            call({"northbound": "requested kick of {}".format(nb_id)}),
        ]
        kick_commands = [
            call("ovnsb_db", ("cluster/kick", "OVN_Southbound", sb_id)),
            call("ovnnb_db", ("cluster/kick", "OVN_Northbound", nb_id)),
        ]

        # Test successfully kicking server from Northbound cluster
        cluster_actions.hookenv.function_get.side_effect = [sb_id, nb_id]

        cluster_actions.kick_server()

        cluster_actions.hookenv.function_fail.assert_not_called()
        cluster_actions.hookenv.function_set.has_calls(expected_func_set_calls)
        cluster_actions.ovn_appctl.has_calls(kick_commands)

        # Reset mocks
        cluster_actions.hookenv.function_set.reset_mock()
        cluster_actions.hookenv.function_fail.reset_mock()
        cluster_actions.ovn_appctl.reset_mock()
        cluster_actions.hookenv.function_get.side_effect = [sb_id, nb_id]

        # Test failure to kick server from Northbound cluster
        process_output = "Failed to kick server"
        exception = cluster_actions.CalledProcessError(
            -1, "/usr/sbin/ovs-appctl", process_output
        )
        cluster_actions.ovn_appctl.side_effect = exception
        errors = [
            call(
                "Failed to kick Southbound cluster member {}: {}".format(
                    sb_id, process_output
                )
            ),
            call(
                "Failed to kick Northbound cluster member {}: {}".format(
                    nb_id, process_output
                )
            ),
        ]

        cluster_actions.kick_server()

        cluster_actions.hookenv.function_set.assert_not_called()
        cluster_actions.hookenv.function_fail.has_calls(errors)
        cluster_actions.ovn_appctl.has_calls(kick_commands)

    @patch.object(cluster_actions.reactive, "endpoint_from_flag")
    @patch.object(cluster_actions, "kick_server")
    @patch.object(cluster_actions, "cluster_status")
    def test_main_no_cluster(self, cluster_status, kick_server, endpoint):
        """Test refusal to run action if unit is not in cluster."""
        endpoint.return_value = None
        err = "Unit is not part of an OVN cluster."

        cluster_actions.main([])

        cluster_actions.hookenv.function_fail.assert_called_once_with(err)
        cluster_status.assert_not_called()
        kick_server.assert_not_called()

    @patch.object(cluster_actions.reactive, "endpoint_from_flag")
    @patch.object(cluster_actions, "kick_server")
    @patch.object(cluster_actions, "cluster_status")
    def test_main_unknown_action(self, cluster_status, kick_server, endpoint):
        """Test executing unknown action from main function."""
        endpoint.return_value = MagicMock()
        action = "unknown-action"
        action_path = (
            "/var/lib/juju/agents/unit-ovn-central-0/charm/actions/" + action
        )
        err = "Action {} undefined".format(action)

        result = cluster_actions.main([action_path])

        self.assertEquals(result, err)

        cluster_status.assert_not_called()
        kick_server.assert_not_called()

    @patch.object(cluster_actions.reactive, "endpoint_from_flag")
    def test_main_cluster_kick(self, endpoint):
        """Test executing cluster-kick action from main function."""
        endpoint.return_value = MagicMock()
        action = "cluster-kick"
        action_path = (
            "/var/lib/juju/agents/unit-ovn-central-0/charm/actions/" + action
        )

        cluster_actions.main([action_path])

        cluster_actions.hookenv.function_fail.assert_not_called()
        self.mapped_action_kick_server.assert_called_once_with()

    @patch.object(cluster_actions.reactive, "endpoint_from_flag")
    def test_main_cluster_status(self, endpoint):
        """Test executing cluster-status action from main function."""
        endpoint.return_value = MagicMock()
        action = "cluster-status"
        action_path = (
            "/var/lib/juju/agents/unit-ovn-central-0/charm/actions/" + action
        )

        cluster_actions.main([action_path])

        cluster_actions.hookenv.function_fail.assert_not_called()
        self.mapped_action_cluster_status.assert_called_once_with()
