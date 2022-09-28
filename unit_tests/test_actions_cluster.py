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

from copy import deepcopy
from unittest import TestCase
from unittest.mock import MagicMock, patch, call

import yaml

import actions.cluster as cluster_actions


class ClusterActionTests(TestCase):

    UNIT_MAPPING = {
        "ovn-central/0": {"id": "aa11", "address": "ssl:10.0.0.1:6644"},
        "ovn-central/1": {"id": "bb22", "address": "ssl:10.0.0.2:6644"},
        "ovn-central/2": {"id": "cc33", "address": "ssl:10.0.0.3:6644"},
    }

    @property
    def servers(self):
        """Return list of tuples representing servers in cluster.

        This property uses data from self.UNIT_MAPPING to produce output
        similar to that of OVNClusterStatus.servers attribute.

        :rtype: List[Tuple(str, str)]
        """
        servers = []
        for server in self.UNIT_MAPPING.values():
            servers.append((server["id"], server["address"]))
        return servers

    @property
    def unit_ip_map(self):
        """Return mapping between unit names and their IPs.

        This property uses data from self.UNIT_MAPPING.

        :rtype: Dict[str, str]
        """
        unit_map = {}
        for unit, data in self.UNIT_MAPPING.items():
            unit_map[unit] = data["address"].split(":")[1]
        return unit_map

    @property
    def unit_id_map(self):
        """Return mapping between unit names and their IDs.

        This property uses data from self.UNIT_MAPPING.

        :rtype: Dict[str, str]
        """
        unit_map = {}
        for unit, data in self.UNIT_MAPPING.items():
            unit_map[unit] = data["id"]
        return unit_map

    def setUp(self):
        """Setup and clean up frequent mocks."""
        super().setUp()
        mocks = [
            patch.object(cluster_actions.ch_core.hookenv, "action_get"),
            patch.object(cluster_actions.ch_core.hookenv, "action_set"),
            patch.object(cluster_actions.ch_core.hookenv, "action_fail"),
            patch.object(cluster_actions.ch_ovn, "ovn_appctl"),
        ]

        for mock in mocks:
            mock.start()
            self.addCleanup(mock.stop)

        # Mock actions mapped in the cluster.py otherwise they'd refer
        # to non-mocked functions.
        self.mapped_action_cluster_kick = MagicMock()
        self.mapped_action_cluster_status = MagicMock()
        cluster_actions.ACTIONS[
            "cluster-kick"
        ] = self.mapped_action_cluster_kick
        cluster_actions.ACTIONS[
            "cluster-status"
        ] = self.mapped_action_cluster_status

    def test_url_to_ip(self):
        """Test function that parses IPs out of server URLs."""
        valid_ipv4 = "10.0.0.1"
        valid_ipv6 = "2001:db8:3333:4444:5555:6666:7777:8888"
        invalid_addr = "foo"
        url = "ssl:{}:6644"

        # Parse valid IPv4
        ipv4 = cluster_actions._url_to_ip(url.format(valid_ipv4))
        self.assertEquals(ipv4, valid_ipv4)

        # Parse valid IPv6
        ipv6 = cluster_actions._url_to_ip(url.format(valid_ipv6))
        self.assertEquals(ipv6, valid_ipv6)

        # Parse invalid url
        cluster_actions.ch_ip.is_ip.return_value = False
        with self.assertRaises(cluster_actions.StatusParsingException):
            cluster_actions._url_to_ip(url.format(invalid_addr))

    @patch.object(cluster_actions.ch_ovn, 'OVNClusterStatus')
    def test_format_cluster_status(self, mock_cluster_status):
        """Test turning OVNClusterStatus into dict.

        Resulting dict also contains additional info mapping cluster servers
        to the juju units.
        """
        sample_data = {"cluster_id": "11aa", "servers": self.servers}
        mock_cluster_status.to_yaml.return_value = sample_data
        mock_cluster_status.servers = self.servers

        cluster_status = cluster_actions._format_cluster_status(
            mock_cluster_status, self.unit_ip_map
        )
        # Compare resulting dict with expected data
        expected_status = sample_data.copy()
        expected_status["unit_map"] = self.unit_id_map
        self.assertEquals(cluster_status, expected_status)

    @patch.object(cluster_actions.ch_ovn, 'OVNClusterStatus')
    def test_format_cluster_status_missing_server(self, mock_cluster_status):
        """Test turning OVNClusterStatus into dict with a missing server.

        This use-case happens when OVN cluster reports server that does not run
        on active ovn-central unit. For example, if server ran on unit that was
        destroyed and did not leave cluster gracefully. in such case, resulting
        status shows "Unit" attribute of this server as "UNKNOWN"
        """
        missing_server_id = "ff99"
        missing_server_ip = "10.0.0.99"
        missing_server_url = "ssl:{}:6644".format(missing_server_ip)
        servers = self.servers.copy()
        servers.append((missing_server_id, missing_server_url))

        sample_data = {"cluster_id": "11aa", "servers": servers}
        mock_cluster_status.to_yaml.return_value = sample_data
        mock_cluster_status.servers = servers

        cluster_status = cluster_actions._format_cluster_status(
            mock_cluster_status, self.unit_ip_map
        )
        # Compare resulting dict with expected data
        expected_status = sample_data.copy()
        expected_status["unit_map"] = self.unit_id_map
        expected_status["unit_map"]["UNKNOWN"] = [missing_server_id]

        self.assertEquals(cluster_status, expected_status)

    @patch.object(cluster_actions.ch_ovn, 'OVNClusterStatus')
    @patch.object(cluster_actions, "_url_to_ip")
    def test_format_cluster_parsing_failure(
            self,
            mock_url_to_ip,
            mock_cluster_status
    ):
        """Test failure to parse status with format_cluster_status()."""
        sample_data = {"cluster_id": "11aa", "servers": self.servers}
        mock_cluster_status.to_yaml.return_value = sample_data
        mock_cluster_status.servers = self.servers
        mock_url_to_ip.side_effect = cluster_actions.StatusParsingException

        with self.assertRaises(cluster_actions.StatusParsingException):
            cluster_actions._format_cluster_status(
                mock_cluster_status, self.unit_ip_map
            )

    @patch.object(cluster_actions.reactive, "endpoint_from_flag")
    @patch.object(cluster_actions.ch_core.hookenv, "local_unit")
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

    def test_kick_server_success(self):
        """Test successfully kicking server from cluster"""
        server_id = "aa11"
        expected_sb_call = (
            "ovnsb_db",
            ("cluster/kick", "OVN_Southbound", server_id)
        )
        expected_nb_call = (
            "ovnnb_db",
            ("cluster/kick", "OVN_Northbound", server_id)
        )

        # test kick from Southbound cluster
        cluster_actions._kick_server("southbound", server_id)
        cluster_actions.ch_ovn.ovn_appctl.assert_called_once_with(
            *expected_sb_call
        )

        # Reset mock
        cluster_actions.ch_ovn.ovn_appctl.reset_mock()

        # test kick from Northbound cluster
        cluster_actions._kick_server("northbound", server_id)
        cluster_actions.ch_ovn.ovn_appctl.assert_called_once_with(
            *expected_nb_call
        )

    def test_kick_server_unknown_cluster(self):
        """Test failure when kicking server from unknown cluster.

        Function _kick_server() expects either "southbound" or "northbound" as
        value of 'cluster' parameter. Other values should raise ValueError.
        """
        with self.assertRaises(ValueError):
            cluster_actions._kick_server("foo", "11aa")

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
            nb_cluster_status,
        ]

        # Test successfully generating cluster status
        cluster_actions.cluster_status()

        expected_calls = [
            call(
                {
                    "ovnsb": yaml.safe_dump(
                        sb_cluster_status, sort_keys=False
                    )
                }
            ),
            call(
                {
                    "ovnnb": yaml.dump(
                        nb_cluster_status, sort_keys=False
                    )
                }
            ),
        ]
        cluster_actions.ch_core.hookenv.action_set.has_calls(expected_calls)
        cluster_actions.ch_core.hookenv.action_fail.asser_not_called()

        # Reset mocks
        cluster_actions.ch_core.hookenv.action_set.reset_mock()

        # Test failure to generate cluster status
        msg = "parsing failed"
        format_cluster_mock.side_effect = (
            cluster_actions.StatusParsingException(msg)
        )

        cluster_actions.cluster_status()

        cluster_actions.ch_core.hookenv.action_set.assert_not_called()
        cluster_actions.ch_core.hookenv.action_fail.assert_called_once_with(
            msg
        )

    @patch.object(cluster_actions, "_kick_server")
    def test_cluster_kick_no_server(self, kick_server_mock):
        """Test running cluster-kick action without providing any server ID."""
        cluster_actions.ch_core.hookenv.action_get.return_value = ""
        err = "At least one server ID to kick must be specified."

        cluster_actions.cluster_kick()

        cluster_actions.ch_core.hookenv.action_fail.assert_called_once_with(
            err
        )
        cluster_actions.ch_core.hookenv.action_set.assert_not_called()
        kick_server_mock.assert_not_called()

    @patch.object(cluster_actions, "_kick_server")
    def test_cluster_kick_sb_server(self, kick_server_mock):
        """Test kicking single Southbound server from cluster."""
        sb_id = "11aa"
        nb_id = ""
        expected_msg = {"ovnsb": "requested kick of {}".format(sb_id)}

        # Test successfully kicking server from Southbound cluster
        cluster_actions.ch_core.hookenv.action_get.side_effect = [
            sb_id,
            nb_id,
        ]

        cluster_actions.cluster_kick()

        cluster_actions.ch_core.hookenv.action_fail.assert_not_called()
        cluster_actions.ch_core.hookenv.action_set.assert_called_once_with(
            expected_msg
        )
        kick_server_mock.assert_called_once_with("southbound", sb_id)

        # Reset mocks
        cluster_actions.ch_core.hookenv.action_set.reset_mock()
        cluster_actions.ch_core.hookenv.action_fail.reset_mock()
        kick_server_mock.reset_mock()
        cluster_actions.ch_core.hookenv.action_get.side_effect = [
            sb_id,
            nb_id,
        ]

        # Test failure to kick server from Southbound cluster
        process_output = "Failed to kick server"
        exception = cluster_actions.subprocess.CalledProcessError(
            -1, "/usr/sbin/ovs-appctl", process_output
        )
        kick_server_mock.side_effect = exception
        err = "Failed to kick Southbound cluster member {}: {}".format(
            sb_id, process_output
        )

        cluster_actions.cluster_kick()

        cluster_actions.ch_core.hookenv.action_set.assert_not_called()
        cluster_actions.ch_core.hookenv.action_fail.assert_called_once_with(
            err
        )
        kick_server_mock.assert_called_once_with("southbound", sb_id)

    @patch.object(cluster_actions, "_kick_server")
    def test_cluster_kick_nb_server(self, kick_server_mock):
        """Test kicking single Northbound server from cluster."""
        sb_id = ""
        nb_id = "22bb"
        expected_msg = {"ovnnb": "requested kick of {}".format(nb_id)}

        # Test successfully kicking server from Northbound cluster
        cluster_actions.ch_core.hookenv.action_get.side_effect = [
            sb_id,
            nb_id,
        ]

        cluster_actions.cluster_kick()

        cluster_actions.ch_core.hookenv.action_fail.assert_not_called()
        cluster_actions.ch_core.hookenv.action_set.assert_called_once_with(
            expected_msg
        )
        kick_server_mock.assert_called_once_with("northbound", nb_id)

        # Reset mocks
        cluster_actions.ch_core.hookenv.action_set.reset_mock()
        cluster_actions.ch_core.hookenv.action_fail.reset_mock()
        kick_server_mock.reset_mock()
        cluster_actions.ch_core.hookenv.action_get.side_effect = [
            sb_id,
            nb_id,
        ]

        # Test failure to kick server from Northbound cluster
        process_output = "Failed to kick server"
        exception = cluster_actions.subprocess.CalledProcessError(
            -1, "/usr/sbin/ovs-appctl", process_output
        )
        kick_server_mock.side_effect = exception
        err = "Failed to kick Northbound cluster member {}: {}".format(
            nb_id, process_output
        )

        cluster_actions.cluster_kick()

        cluster_actions.ch_core.hookenv.action_set.assert_not_called()
        cluster_actions.ch_core.hookenv.action_fail.assert_called_once_with(
            err
        )
        kick_server_mock.assert_called_once_with("northbound", nb_id)

    @patch.object(cluster_actions, "_kick_server")
    def test_cluster_kick_both_server(self, kick_server_mock):
        """Test kicking Southbound and Northbound servers from cluster."""
        sb_id = "11bb"
        nb_id = "22bb"
        expected_func_set_calls = [
            call({"ovnsb": "requested kick of {}".format(sb_id)}),
            call({"ovnnb": "requested kick of {}".format(nb_id)}),
        ]
        kick_commands = [
            call("southbound", sb_id),
            call("northbound", nb_id),
        ]

        # Test successfully kicking servers from Northbound and Southbound
        # cluster
        cluster_actions.ch_core.hookenv.action_get.side_effect = [
            sb_id,
            nb_id,
        ]

        cluster_actions.cluster_kick()

        cluster_actions.ch_core.hookenv.action_fail.assert_not_called()
        cluster_actions.ch_core.hookenv.action_set.has_calls(
            expected_func_set_calls
        )
        kick_server_mock.has_calls(kick_commands)

        # Reset mocks
        cluster_actions.ch_core.hookenv.action_set.reset_mock()
        cluster_actions.ch_core.hookenv.action_fail.reset_mock()
        cluster_actions.ch_ovn.ovn_appctl.reset_mock()
        cluster_actions.ch_core.hookenv.action_get.side_effect = [
            sb_id,
            nb_id,
        ]

        # Test failure to kick servers from Northbound and Southbound
        # clusters
        process_output = "Failed to kick server"
        exception = cluster_actions.subprocess.CalledProcessError(
            -1, "/usr/sbin/ovs-appctl", process_output
        )
        kick_server_mock.side_effect = exception
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

        cluster_actions.cluster_kick()

        cluster_actions.ch_core.hookenv.action_set.assert_not_called()
        cluster_actions.ch_core.hookenv.action_fail.has_calls(errors)
        kick_server_mock.has_calls(kick_commands)

    @patch.object(cluster_actions.reactive, "endpoint_from_flag")
    def test_main_no_cluster(self, endpoint):
        """Test refusal to run action if unit is not in cluster."""
        endpoint.return_value = None
        err = "Unit is not part of an OVN cluster."

        cluster_actions.main([])

        cluster_actions.ch_core.hookenv.action_fail.assert_called_once_with(
            err
        )
        self.mapped_action_cluster_kick.assert_not_called()
        self.mapped_action_cluster_status.assert_not_called()

    @patch.object(cluster_actions.reactive, "endpoint_from_flag")
    def test_main_unknown_action(self, endpoint):
        """Test executing unknown action from main function."""
        endpoint.return_value = MagicMock()
        action = "unknown-action"
        action_path = (
            "/var/lib/juju/agents/unit-ovn-central-0/charm/actions/" + action
        )
        err = "Action {} undefined".format(action)

        result = cluster_actions.main([action_path])

        self.assertEquals(result, err)

        self.mapped_action_cluster_kick.assert_not_called()
        self.mapped_action_cluster_status.assert_not_called()

    @patch.object(cluster_actions.reactive, "endpoint_from_flag")
    def test_main_cluster_kick(self, endpoint):
        """Test executing cluster-kick action from main function."""
        endpoint.return_value = MagicMock()
        action = "cluster-kick"
        action_path = (
            "/var/lib/juju/agents/unit-ovn-central-0/charm/actions/" + action
        )

        cluster_actions.main([action_path])

        cluster_actions.ch_core.hookenv.action_fail.assert_not_called()
        self.mapped_action_cluster_kick.assert_called_once_with()

    @patch.object(cluster_actions.reactive, "endpoint_from_flag")
    def test_main_cluster_status(self, endpoint):
        """Test executing cluster-status action from main function."""
        endpoint.return_value = MagicMock()
        action = "cluster-status"
        action_path = (
            "/var/lib/juju/agents/unit-ovn-central-0/charm/actions/" + action
        )

        cluster_actions.main([action_path])

        cluster_actions.ch_core.hookenv.action_fail.assert_not_called()
        self.mapped_action_cluster_status.assert_called_once_with()
