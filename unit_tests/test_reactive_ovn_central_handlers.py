# Copyright 2019 Canonical Ltd
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

import mock

import reactive.ovn_central_handlers as handlers

import charms_openstack.test_utils as test_utils


class TestRegisteredHooks(test_utils.TestRegisteredHooks):

    def test_hooks(self):
        defaults = [
            'config.changed',
            'charm.default-select-release',
            'update-status',
            'upgrade-charm',
        ]
        hook_set = {
            'when_none': {
                'announce_leader_ready': ('is-update-status-hook',
                                          'leadership.set.nb_cid',
                                          'leadership.set.sb_cid',
                                          'coordinator.granted.upgrade',
                                          'coordinator.requested.upgrade',
                                          'config.changed.source',
                                          'config.changed.ovn-source'),
                'configure_firewall': ('is-update-status-hook',),
                'enable_default_certificates': ('is-update-status-hook',
                                                'leadership.is_leader',),
                'initialize_firewall': ('is-update-status-hook',
                                        'charm.firewall_initialized',),
                'initialize_ovsdbs': ('is-update-status-hook',
                                      'leadership.set.nb_cid',
                                      'leadership.set.sb_cid',
                                      'coordinator.granted.upgrade',
                                      'coordinator.requested.upgrade'),
                'maybe_do_upgrade': ('is-update-status-hook',),
                'maybe_request_upgrade': ('is-update-status-hook',),
                'publish_addr_to_clients': ('is-update-status-hook',),
                'render': ('is-update-status-hook',
                           'coordinator.granted.upgrade',
                           'coordinator.requested.upgrade',
                           'config.changed.source',
                           'config.changed.ovn-source'),
                'configure_nrpe': ('charm.paused', 'is-update-status-hook',),
                'stamp_fresh_deployment': ('charm.installed',
                                           'leadership.set.install_stamp'),
                'stamp_upgraded_deployment': ('is-update-status-hook',
                                              'leadership.set.install_stamp',
                                              'leadership.set.upgrade_stamp'),
                'enable_install': ('charm.installed', 'is-update-status-hook'),
                'reassess_exporter': ('is-update-status-hook',),
                'maybe_clear_metrics_endpoint': ('is-update-status-hook',),
                'handle_metrics_endpoint': ('is-update-status-hook',),
            },
            'when': {
                'announce_leader_ready': ('config.rendered',
                                          'certificates.connected',
                                          'certificates.available',
                                          'leadership.is_leader',
                                          'ovsdb-peer.connected',),
                'certificates_in_config_tls': ('config.rendered',
                                               'config.changed',),
                'configure_firewall': ('ovsdb-peer.available',),
                'enable_default_certificates': ('charm.installed',),
                'initialize_ovsdbs': ('charm.installed',
                                      'leadership.is_leader',
                                      'ovsdb-peer.connected',),
                'maybe_do_upgrade': ('ovsdb-peer.available',
                                     'coordinator.granted.upgrade',),
                'maybe_request_upgrade': ('ovsdb-peer.available',),
                'publish_addr_to_clients': ('ovsdb-peer.available',
                                            'leadership.set.nb_cid',
                                            'leadership.set.sb_cid',
                                            'certificates.connected',
                                            'certificates.available',),
                'render': ('ovsdb-peer.available',
                           'leadership.set.nb_cid',
                           'leadership.set.sb_cid',
                           'certificates.connected',
                           'certificates.available',),
                'configure_nrpe': ('config.rendered',),
                'stamp_fresh_deployment': ('leadership.is_leader',),
                'stamp_upgraded_deployment': ('charm.installed',
                                              'leadership.is_leader'),
                'handle_metrics_endpoint': (
                    'charm.installed',
                    'metrics-endpoint.available',
                    'snap.installed.prometheus-ovn-exporter',
                ),
                'reassess_exporter': (
                    'charm.installed',
                ),
                'maybe_clear_metrics_endpoint': (
                    'charm.installed',
                    'metrics-endpoint.available',
                ),
            },
            'when_any': {
                'configure_nrpe': ('config.changed.nagios_context',
                                   'config.changed.nagios_servicegroups',
                                   'endpoint.nrpe-external-master.changed',
                                   'nrpe-external-master.available',),
                'enable_install': ('leadership.set.install_stamp',
                                   'leadership.set.upgrade_stamp'),
                'maybe_request_upgrade': ('config.changed.source',
                                          'config.changed.ovn-source'),
                'reassess_exporter': (
                    'config.changed.ovn-exporter-channel',
                    'snap.installed.prometheus-ovn-exporter'),
            },
            'when_not': {
                'configure_deferred_restarts': ('is-update-status-hook',),
                'maybe_clear_metrics_endpoint': (
                    'snap.installed.prometheus-ovn-exporter',
                ),
            },
        }
        # test that the hooks were registered via the
        # reactive.ovn_handlers
        self.registered_hooks_test_helper(handlers, hook_set, defaults)


class TestOvnCentralHandlers(test_utils.PatchHelper):

    def setUp(self):
        super().setUp()
        self.target = mock.MagicMock()
        self.patch_object(handlers.charm, 'provide_charm_instance',
                          new=mock.MagicMock())
        self.provide_charm_instance().__enter__.return_value = \
            self.target
        self.provide_charm_instance().__exit__.return_value = None

    def test_initialize_firewall(self):
        self.patch_object(handlers.reactive, 'set_flag')
        handlers.initialize_firewall()
        self.target.initialize_firewall.assert_called_once_with()
        self.set_flag.assert_called_once_with('charm.firewall_initialized')

    def test_announce_leader_ready(self):
        self.patch_object(handlers.reactive, 'endpoint_from_name')
        self.patch_object(handlers.reactive, 'endpoint_from_flag')
        self.patch_object(handlers.leadership, 'leader_set')
        ovsdb = mock.MagicMock()
        self.endpoint_from_name.return_value = ovsdb
        ovsdb_peer = mock.MagicMock()
        self.endpoint_from_flag.return_value = ovsdb_peer
        cluster_status = mock.MagicMock()
        cluster_status.cluster_id = 'fake-uuid'
        self.target.cluster_status.return_value = cluster_status
        handlers.announce_leader_ready()
        ovsdb_peer.publish_cluster_local_addr.assert_called_once_with()
        self.target.configure_ovn.assert_called_once_with(
            ovsdb_peer.db_nb_port,
            ovsdb.db_sb_port,
            ovsdb_peer.db_sb_admin_port)

        self.leader_set.assert_called_once_with(
            {
                'ready': True,
                'nb_cid': 'fake-uuid',
                'sb_cid': 'fake-uuid',
            })

    def test_initialize_ovsdbs(self):
        self.patch_object(handlers.reactive, 'endpoint_from_flag')
        self.patch_object(handlers.charm, 'use_defaults')
        self.patch_object(handlers.reactive, 'set_flag')
        ovsdb_peer = mock.MagicMock()
        self.endpoint_from_flag.return_value = ovsdb_peer
        handlers.initialize_ovsdbs()
        self.target.render_with_interfaces.assert_called_once_with(
            [ovsdb_peer])
        self.target.enable_services.assert_called_once_with()
        self.use_defaults.assert_called_once_with('certificates.available')
        self.set_flag.assert_called_once_with('config.rendered')
        self.target.assess_status()

    def test_enable_default_certificates(self):
        self.patch_object(handlers.charm, 'use_defaults')
        handlers.enable_default_certificates()
        self.use_defaults.assert_called_once_with('certificates.available')

    def test_configure_firewall(self):
        self.patch_object(handlers.reactive, 'endpoint_from_flag')
        ovsdb_peer = mock.MagicMock()
        self.endpoint_from_flag.side_effect = (ovsdb_peer, None)
        handlers.configure_firewall()
        self.endpoint_from_flag.assert_has_calls([
            mock.call('ovsdb-peer.available'),
            mock.call('ovsdb-cms.connected'),
        ])
        self.target.configure_firewall.assert_called_once_with({
            (ovsdb_peer.db_nb_port,
                ovsdb_peer.db_sb_admin_port,
                ovsdb_peer.db_sb_cluster_port,
                ovsdb_peer.db_nb_cluster_port,):
            ovsdb_peer.cluster_remote_addrs,
            (ovsdb_peer.db_nb_port,
                ovsdb_peer.db_sb_admin_port,): None,
        })
        self.target.assess_status.assert_called_once_with()
        self.target.configure_firewall.reset_mock()
        ovsdb_cms = mock.MagicMock()
        self.endpoint_from_flag.side_effect = (ovsdb_peer, ovsdb_cms)
        handlers.configure_firewall()
        self.target.configure_firewall.assert_called_once_with({
            (ovsdb_peer.db_nb_port,
                ovsdb_peer.db_sb_admin_port,
                ovsdb_peer.db_sb_cluster_port,
                ovsdb_peer.db_nb_cluster_port,):
            ovsdb_peer.cluster_remote_addrs,
            (ovsdb_peer.db_nb_port,
                ovsdb_peer.db_sb_admin_port,): ovsdb_cms.client_remote_addrs,
        })

    def test_publish_addr_to_clients(self):
        self.patch_object(handlers.reactive, 'endpoint_from_flag')
        ovsdb_peer = mock.MagicMock()
        ovsdb_peer.cluster_local_addr = mock.PropertyMock().return_value = (
            'a.b.c.d')
        ovsdb = mock.MagicMock()
        ovsdb_cms = mock.MagicMock()
        self.endpoint_from_flag.side_effect = [ovsdb_peer, ovsdb, ovsdb_cms]
        handlers.publish_addr_to_clients()
        ovsdb.publish_cluster_local_addr.assert_called_once_with('a.b.c.d')
        ovsdb_cms.publish_cluster_local_addr.assert_called_once_with('a.b.c.d')

    def test_render(self):
        self.patch_object(handlers.reactive, 'endpoint_from_name')
        self.patch_object(handlers.reactive, 'endpoint_from_flag')
        self.patch_object(handlers.reactive, 'set_flag')
        ovsdb_peer = mock.MagicMock()
        # re-using the same conection strings for both NB and SB DBs here, the
        # implementation detail is unit tested in the interface
        connection_strs = ('ssl:a.b.c.d:1234',
                           'ssl:e.f.g.h:1234',
                           'ssl:i.j.k.l:1234',)
        ovsdb_peer.db_connection_strs.return_value = connection_strs
        self.endpoint_from_flag.return_value = ovsdb_peer
        self.target.enable_services.return_value = False
        handlers.render()
        self.endpoint_from_flag.assert_called_once_with('ovsdb-peer.available')
        self.target.render_with_interfaces.assert_called_once_with(
            [ovsdb_peer])
        self.target.join_cluster.assert_has_calls([
            mock.call('ovnnb_db.db',
                      'OVN_Northbound',
                      connection_strs,
                      connection_strs),
            mock.call('ovnsb_db.db',
                      'OVN_Southbound',
                      connection_strs,
                      connection_strs),
        ])
        self.target.assess_status.assert_called_once_with()
        self.target.enable_services.return_value = True
        handlers.render()
        self.set_flag.assert_called_once_with('config.rendered')
