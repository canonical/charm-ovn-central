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


import unittest.mock as mock
import actions.os_deferred_event_actions as os_deferred_event_actions
import charms_openstack.test_utils as test_utils


class TestOSDeferredEventActions(test_utils.PatchHelper):

    def setUp(self):
        super().setUp()
        self.patch_object(os_deferred_event_actions.hookenv, 'action_get')
        self.action_config = {}
        self.action_get.side_effect = lambda x: self.action_config.get(x)
        self.patch_object(os_deferred_event_actions.hookenv, 'action_fail')

        self.patch_object(
            os_deferred_event_actions.charms_openstack.charm,
            'provide_charm_instance')
        self.charm_instance = mock.MagicMock()
        self.provide_charm_instance.return_value.__enter__.return_value = \
            self.charm_instance

    def test_restart_services(self):
        self.patch_object(
            os_deferred_event_actions.os_utils,
            'restart_services_action')

        self.action_config = {
            'deferred-only': True,
            'services': ''}
        os_deferred_event_actions.restart_services(['restart-services'])
        self.charm_instance._assess_status.assert_called_once_with()
        self.restart_services_action.assert_called_once_with(
            deferred_only=True)

        self.charm_instance.reset_mock()
        self.restart_services_action.reset_mock()

        self.action_config = {
            'deferred-only': False,
            'services': 'svcA svcB'}
        os_deferred_event_actions.restart_services(['restart-services'])
        self.charm_instance._assess_status.assert_called_once_with()
        self.restart_services_action.assert_called_once_with(
            services=['svcA', 'svcB'])

        self.charm_instance.reset_mock()
        self.restart_services_action.reset_mock()

        self.action_config = {
            'deferred-only': True,
            'services': 'svcA svcB'}
        os_deferred_event_actions.restart_services(['restart-services'])
        self.action_fail.assert_called_once_with(
            'Cannot set deferred-only and services')

        self.charm_instance.reset_mock()
        self.restart_services_action.reset_mock()
        self.action_fail.reset_mock()

        self.action_config = {
            'deferred-only': False,
            'services': ''}
        os_deferred_event_actions.restart_services(['restart-services'])
        self.action_fail.assert_called_once_with(
            'Please specify deferred-only or services')

    def test_show_deferred_events(self):
        self.patch_object(
            os_deferred_event_actions.os_utils,
            'show_deferred_events_action_helper')
        os_deferred_event_actions.show_deferred_events(
            ['show-deferred-events'])
        self.show_deferred_events_action_helper.assert_called_once_with()

    def test_run_deferred_hooks(self):
        self.patch_object(
            os_deferred_event_actions.deferred_events,
            'get_deferred_hooks')
        self.patch_object(
            os_deferred_event_actions.reactive,
            'endpoint_from_flag')
        self.patch_object(
            os_deferred_event_actions.reactive,
            'is_flag_set')
        self.patch_object(
            os_deferred_event_actions.charms_openstack.charm,
            'optional_interfaces')
        interfaces_mock = mock.MagicMock()
        self.optional_interfaces.return_value = interfaces_mock
        self.is_flag_set.return_value = True
        ovsdb_available = mock.MagicMock()
        ovsdb_available.db_sb_connection_strs = ['constrA', 'connstrB']
        self.endpoint_from_flag.return_value = ovsdb_available

        self.get_deferred_hooks.return_value = ['install']
        os_deferred_event_actions.run_deferred_hooks(['run-deferred-hooks'])
        self.charm_instance.install.assert_called_once_with(
            check_deferred_events=False)
        self.assertFalse(self.charm_instance.configure_ovs.called)
        self.assertFalse(
            self.charm_instance.render_with_interfaces.called)
        self.charm_instance._assess_status.assert_called_once_with()

        self.charm_instance.reset_mock()

        self.get_deferred_hooks.return_value = ['install', 'configure_ovs']
        os_deferred_event_actions.run_deferred_hooks(['run-deferred-hooks'])
        self.charm_instance.install.assert_called_once_with(
            check_deferred_events=False)
        self.charm_instance.render_with_interfaces.assert_called_once_with(
            interfaces_mock)
        self.charm_instance.configure_ovs.assert_called_once_with(
            'constrA,connstrB',
            True,
            check_deferred_events=False)
        self.charm_instance._assess_status.assert_called_once_with()

        self.charm_instance.reset_mock()

        self.get_deferred_hooks.return_value = []
        os_deferred_event_actions.run_deferred_hooks(['run-deferred-hooks'])
        self.assertFalse(self.charm_instance.install.configure_ovs.called)
        self.assertFalse(self.charm_instance.configure_ovs.called)
        self.assertFalse(self.charm_instance.render_with_interfaces.called)
        self.charm_instance._assess_status.assert_called_once_with()
