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
            'charm.installed',
            'config.changed',
            'update-status',
            'upgrade-charm',
        ]
        hook_set = {
            'when_not_all': {
                'certificates_in_config_tls': ('config.default.ssl_ca',
                                               'config.default.ssl_cert',
                                               'config.default.ssl_key',),
            },
            'when_not': {
                'enable_default_certificates': ('leadership.is_leader',),
                'initialize_ovsdbs': ('leadership.set.ready',),
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
                'publish_addr_to_clients': ('ovsdb-peer.available',
                                            'leadership.set.ready',
                                            'certificates.connected',
                                            'certificates.available',),
                'render': ('ovsdb-peer.available',
                           'leadership.set.ready',
                           'certificates.connected',
                           'certificates.available',),
            },
        }
        # test that the hooks were registered via the
        # reactive.ovn_handlers
        self.registered_hooks_test_helper(handlers, hook_set, defaults)


class TestOvnCentralHandlers(test_utils.PatchHelper):

    def setUp(self):
        super().setUp()
        # self.patch_release(octavia.OctaviaCharm.release)
        self.charm = mock.MagicMock()
        self.patch_object(handlers.charm, 'provide_charm_instance',
                          new=mock.MagicMock())
        self.provide_charm_instance().__enter__.return_value = \
            self.charm
        self.provide_charm_instance().__exit__.return_value = None

    def test_announce_leader_ready(self):
        self.patch_object(handlers.reactive, 'endpoint_from_flag')
        self.patch_object(handlers.leadership, 'leader_set')
        ovsdb_peer = mock.MagicMock()
        self.endpoint_from_flag.return_value = ovsdb_peer
        handlers.announce_leader_ready()
        ovsdb_peer.publish_cluster_local_addr.assert_called_once_with()
        self.leader_set.assert_called_once_with({'ready': True})

    def test_initialize_ovsdbs(self):
        self.patch_object(handlers.reactive, 'endpoint_from_flag')
        self.patch_object(handlers.charm, 'use_defaults')
        self.patch_object(handlers.reactive, 'set_flag')
        ovsdb_peer = mock.MagicMock()
        self.endpoint_from_flag.return_value = ovsdb_peer
        handlers.initialize_ovsdbs()
        self.charm.render_with_interfaces.assert_called_once_with([ovsdb_peer])
        self.charm.enable_services.assert_called_once_with()
        self.use_defaults.assert_called_once_with('certificates.available')
        self.set_flag.assert_called_once_with('config.rendered')
        self.charm.assess_status()

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
        self.charm.configure_firewall.assert_called_once_with({
            (ovsdb_peer.db_nb_port,
                ovsdb_peer.db_sb_admin_port,
                ovsdb_peer.db_sb_cluster_port,
                ovsdb_peer.db_nb_cluster_port,):
            ovsdb_peer.cluster_remote_addrs,
            (ovsdb_peer.db_nb_port,
                ovsdb_peer.db_sb_port,): None,
        })
        self.charm.assess_status.assert_called_once_with()
        self.charm.configure_firewall.reset_mock()
        ovsdb_cms = mock.MagicMock()
        self.endpoint_from_flag.side_effect = (ovsdb_peer, ovsdb_cms)
        handlers.configure_firewall()
        self.charm.configure_firewall.assert_called_once_with({
            (ovsdb_peer.db_nb_port,
                ovsdb_peer.db_sb_admin_port,
                ovsdb_peer.db_sb_cluster_port,
                ovsdb_peer.db_nb_cluster_port,):
            ovsdb_peer.cluster_remote_addrs,
            (ovsdb_peer.db_nb_port,
                ovsdb_peer.db_sb_port,):
            ovsdb_cms.client_remote_addrs,
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
        self.charm.enable_services.return_value = False
        handlers.render()
        self.endpoint_from_flag.assert_called_once_with('ovsdb-peer.available')
        self.charm.render_with_interfaces.assert_called_once_with([ovsdb_peer])
        self.charm.join_cluster.assert_has_calls([
            mock.call('/var/lib/openvswitch/ovnnb_db.db',
                      'OVN_Northbound',
                      connection_strs,
                      connection_strs),
            mock.call('/var/lib/openvswitch/ovnsb_db.db',
                      'OVN_Southbound',
                      connection_strs,
                      connection_strs),
        ])
        self.charm.assess_status.assert_called_once_with()
        self.charm.enable_services.return_value = True
        handlers.render()
        self.set_flag.assert_called_once_with('config.rendered')
