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
import check_ovn_db_connections as check
import nagios_plugin3 as nagios


class TestCheckOVNDBConnections(test_utils.PatchHelper):
    @mock.patch("os.path.exists")
    def test_parse_output_does_not_exist(self, mock_exists):
        mock_exists.return_value = False
        self.assertRaises(nagios.UnknownError, check.parse_output)

    @mock.patch("os.path.exists")
    def test_parse_output_permission_error(self, mock_exists):
        mock_exists.return_value = True
        mock_file = mock.mock_open()
        mock_file.side_effect = PermissionError
        with mock.patch("builtins.open", mock_file) as mocked_open:
            mocked_open.side_effect = PermissionError()
            self.assertRaises(nagios.UnknownError, check.parse_output)

    @mock.patch("os.path.exists")
    def test_parse_output_alert(self, mock_exists):
        mock_exists.return_value = True
        mock_file = mock.mock_open(read_data="CRITICAL: fake error")
        with mock.patch("builtins.open", mock_file):
            self.assertRaises(nagios.UnknownError, check.parse_output)

    @mock.patch("os.path.exists")
    def test_parse_output_ok(self, mock_exists):
        mock_exists.return_value = True
        mock_file = mock.mock_open(
            read_data="OK: OVN DB connections are normal"
        )
        with mock.patch("builtins.open", mock_file):
            # it shouldn't raise any exceptions
            try:
                check.parse_output()
            except Exception as e:
                self.fail("exception raised: {}".format(e))
