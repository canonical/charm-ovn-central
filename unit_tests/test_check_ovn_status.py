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
import unittest

import mock

nagios_plugin3 = mock.MagicMock()
sys.modules["nagios_plugin3"] = nagios_plugin3
nagios_plugin3.UnknownError.side_effect = Exception("UnknownError")
nagios_plugin3.CriticalError.side_effect = Exception("CriticalError")

sys.path.append("src/files")  # noqa
from check_ovn_status import NRPEBase  # noqa


class MockArgs:
    """Mock replacement for argparse."""

    db = "nb"


class TestNRPEBase(unittest.TestCase):
    """Tests for NRPEBase class."""

    args = MockArgs()

    @mock.patch("os.path.exists")
    def test_nb_cmds(self, mock_os):
        """Test that the right command is returned based on socket location."""
        mock_os.side_effect = [True, False, True, False, False]
        nrpe = NRPEBase(self.args)
        commands = nrpe.cmds["nb"]
        self.assertTrue("/var/run/ovn/ovnnb_db.ctl" in commands)
        commands = nrpe.cmds["nb"]
        self.assertTrue("/var/run/openvswitch/ovnnb_db.ctl" in commands)
        with self.assertRaisesRegex(Exception, "UnknownError"):
            commands = nrpe.cmds["nb"]

    @mock.patch("os.path.exists")
    def test_sb_cmds(self, mock_os):
        """Test that the right command is returned based on socket location."""
        mock_os.side_effect = [True, False, True, False, False]
        self.args.db = 'sb'
        nrpe = NRPEBase(self.args)
        commands = nrpe.cmds["sb"]
        self.assertTrue("/var/run/ovn/ovnsb_db.ctl" in commands)
        commands = nrpe.cmds["sb"]
        self.assertTrue("/var/run/openvswitch/ovnsb_db.ctl" in commands)
        with self.assertRaisesRegex(Exception, "UnknownError"):
            commands = nrpe.cmds["sb"]

    @mock.patch("os.path.exists")
    @mock.patch("subprocess.check_output")
    def test_get_db_status(self, mock_check_output, mock_os):
        """Test status output is parsed correctly."""
        mock_os.return_value = True
        # read file
        with open("unit_tests/artifacts/ovn-nb-status.txt", "rb") as ovnstatus:
            mock_check_output.return_value = ovnstatus.read()
        # run get_db_status
        nrpe = NRPEBase(self.args)
        result = nrpe.get_db_status()
        # check result is True
        self.assertTrue(result)

    @mock.patch("os.path.exists")
    @mock.patch("subprocess.check_output")
    def test_get_bad_db_status(self, mock_check_output, mock_os):
        """Test status output is parsed correctly."""
        mock_os.return_value = True
        with open(
            "unit_tests/artifacts/ovn-nb-status-bad.txt", "rb"
        ) as ovnstatus:
            mock_check_output.return_value = ovnstatus.read()
        nrpe = NRPEBase(self.args)
        with self.assertRaisesRegex(Exception, "CriticalError"):
            nrpe.get_db_status()

    def test_args(self):
        """Test that input args are read correctly."""


if __name__ == "__main__":
    unittest.main()
