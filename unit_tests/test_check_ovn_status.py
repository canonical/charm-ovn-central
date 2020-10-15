#! /usr/bin/env python3
# Copyright 2020 Canonical Ltd
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

"""Unit tests for check_ovn_status Nagios plugin."""

import sys
import textwrap
import unittest

import mock

nagios_plugin3 = mock.MagicMock()
sys.modules["nagios_plugin3"] = nagios_plugin3
nagios_plugin3.UnknownError.side_effect = Exception("UnknownError")
nagios_plugin3.CriticalError.side_effect = Exception("CriticalError")

sys.path.append("./src/files")  # noqa
from check_ovn_status import NRPEBase  # noqa


class MockArgs:
    """Mock replacement for argparse."""

    db = "nb"


class MockOsPathExists:
    """Mock a response to the os.path.exists() call."""

    def __init__(self, ovn=True, ovs=False, controller=False):
        """Do class instance setup with responses for the test."""
        self.ovn = ovn
        self.ovs = ovs
        self.controller = controller

    def os_exists(self, path):
        """Return boolean for path based on test params."""
        ovn_path = "/var/run/ovn"
        ovs_path = "/var/run/openvswitch"

        if "-controller.pid" in path:
            return self.controller
        if ovn_path in path:
            return self.ovn
        if ovs_path in path:
            return self.ovs


class TestNRPEBase(unittest.TestCase):
    """Tests for NRPEBase class."""

    args = MockArgs()

    @mock.patch("os.path.exists")
    def test_ovn_cmds(self, mock_os):
        """Test that the right commands returned for ovn."""
        paths = MockOsPathExists()
        mock_os.side_effect = paths.os_exists
        nrpe = NRPEBase(self.args)
        self.assertTrue("/var/run/ovn/ovnnb_db.ctl" in nrpe.cmds["nb"])
        self.assertTrue("/var/run/ovn/ovnsb_db.ctl" in nrpe.cmds["sb"])

    @mock.patch("os.path.exists")
    def test_ovs_cmds(self, mock_os):
        """Test that the right commands returned for openvswitch."""
        paths = MockOsPathExists(ovn=False, ovs=True)
        mock_os.side_effect = paths.os_exists
        nrpe = NRPEBase(self.args)
        self.assertTrue("/var/run/openvswitch/ovnnb_db.ctl" in nrpe.cmds["nb"])
        self.assertTrue("/var/run/openvswitch/ovnsb_db.ctl" in nrpe.cmds["sb"])

    @mock.patch("os.path.exists")
    def test_no_socket_path(self, mock_os):
        """Test that the no socket path returns Unknown."""
        paths = MockOsPathExists(ovn=False, ovs=False)
        mock_os.side_effect = paths.os_exists
        nrpe = NRPEBase(self.args)
        with self.assertRaisesRegex(Exception, "UnknownError"):
            nrpe.cmds["nb"]

    @mock.patch("builtins.open", mock.mock_open(read_data="1234"))
    @mock.patch("os.path.exists")
    def test_controller_cmds(self, mock_os):
        """Test that the right command is returned for chassis hosts."""
        self.args.db = "sb"
        nrpe = NRPEBase(self.args)
        paths = MockOsPathExists(controller=True)
        mock_os.side_effect = paths.os_exists
        commands = nrpe.cmds["controller"]
        print(commands)
        self.assertTrue("/var/run/ovn/ovn-controller.1234.ctl"
                        in commands)

    @mock.patch("os.path.exists")
    @mock.patch("subprocess.check_output")
    def test_get_db_status(self, mock_check_output, mock_os):
        """Test status output is parsed correctly."""
        paths = MockOsPathExists()
        mock_os.side_effect = paths.os_exists

        good_status = textwrap.dedent(
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
        ).encode()
        mock_check_output.return_value = good_status
        # run get_db_status
        nrpe = NRPEBase(self.args)
        result = nrpe.get_db_status()
        # check result is True
        self.assertTrue(result)

    @mock.patch("os.path.exists")
    @mock.patch("subprocess.check_output")
    def test_get_bad_db_status(self, mock_check_output, mock_os):
        """Test status output is parsed correctly."""
        paths = MockOsPathExists()
        mock_os.side_effect = paths.os_exists
        bad_status = textwrap.dedent(
            """\
            e8c5
            Name: OVN_Northbound
            Cluster ID: 6a8f (6a8f9149-3368-4bae-88c5-d6fe2be9b847)
            Server ID: e8c5 (e8c5232f-864c-4e61-990d-e54c666be4bc)
            Address: ssl:10.5.0.24:6643
            Status: mock bad cluster status
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
        ).encode()
        mock_check_output.return_value = bad_status
        nrpe = NRPEBase(self.args)
        with self.assertRaisesRegex(Exception, "CriticalError"):
            nrpe.get_db_status()

    def test_args(self):
        """Test that input args are read correctly."""


if __name__ == "__main__":
    unittest.main()
