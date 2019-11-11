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

import io
import mock
import os

import charms_openstack.test_utils as test_utils

import charm.openstack.ovn_central as ovn_central


class TestOVNConfigProperties(test_utils.PatchHelper):

    def test_ovn_key(self):
        self.assertEquals(ovn_central.ovn_key(None),
                          os.path.join(ovn_central.OVS_ETCDIR, 'key_host'))

    def test_ovn_cert(self):
        self.assertEquals(ovn_central.ovn_cert(None),
                          os.path.join(ovn_central.OVS_ETCDIR, 'cert_host'))

    def test_ovn_ca_cert(self):
        cls = mock.MagicMock()
        cls.charm_instance.name = mock.PropertyMock().return_value = 'name'
        self.assertEquals(ovn_central.ovn_ca_cert(cls),
                          os.path.join(ovn_central.OVS_ETCDIR, 'name.crt'))


class Helper(test_utils.PatchHelper):

    def setUp(self):
        super().setUp()
        self.patch_release(ovn_central.OVNCentralCharm.release)
        self.target = ovn_central.OVNCentralCharm()

    def patch_target(self, attr, return_value=None):
        mocked = mock.patch.object(self.target, attr)
        self._patches[attr] = mocked
        started = mocked.start()
        started.return_value = return_value
        self._patches_start[attr] = started
        setattr(self, attr, started)


class TestOVNCentralCharm(Helper):

    def test_install(self):
        self.patch_object(ovn_central.charms_openstack.charm.OpenStackCharm,
                          'install')
        self.patch_object(ovn_central.os.path, 'islink')
        self.islink.return_value = False
        self.patch_object(ovn_central.os, 'symlink')
        self.patch_target('configure_source')
        self.target.install()
        calls = []
        for service in ('openvswitch-switch', 'ovs-vswitchd', 'ovsdb-server',
                        self.target.services[0],):
            calls.append(
                mock.call('/etc/systemd/system/{}.service'.format(service)))
        self.islink.assert_has_calls(calls)
        calls = []
        for service in ('openvswitch-switch', 'ovs-vswitchd', 'ovsdb-server',
                        self.target.services[0],):
            calls.append(
                mock.call('/dev/null',
                          '/etc/systemd/system/{}.service'.format(service)))
        self.symlink.assert_has_calls(calls)
        self.install.assert_called_once_with()
        self.configure_source.assert_called_once_with()

    def test__default_port_list(self):
        self.assertEquals(
            self.target._default_port_list(),
            [6641, 6642])

    def test_ports_to_check(self):
        self.target._default_port_list = mock.MagicMock()
        self.target.ports_to_check()
        self.target._default_port_list.assert_called_once_with()

    def test_enable_services(self):
        self.patch_object(ovn_central.ch_core.host, 'service_resume')
        self.target.check_if_paused = mock.MagicMock()
        self.target.check_if_paused.return_value = ('status', 'message')
        self.target.enable_services()
        self.target.check_if_paused.assert_called_once_with()
        self.assertFalse(self.service_resume.called)
        self.target.check_if_paused.return_value = (None, None)
        self.target.enable_services()
        calls = []
        for service in self.target.services:
            calls.append(mock.call(service))
        self.service_resume.assert_has_calls(calls)

    def test_run(self):
        self.patch_object(ovn_central.subprocess, 'run')
        self.patch_object(ovn_central.ch_core.hookenv, 'log')
        self.target.run('some', 'args')
        self.run.assert_called_once_with(
            ('some', 'args'),
            stdout=ovn_central.subprocess.PIPE,
            stderr=ovn_central.subprocess.STDOUT,
            check=True,
            universal_newlines=True)

    def test_join_cluster(self):
        self.patch_target('run')
        self.target.join_cluster('/a/db.file',
                                 'aSchema',
                                 ['ssl:a.b.c.d:1234'],
                                 ['ssl:e.f.g.h:1234', 'ssl:i.j.k.l:1234'])
        self.run.assert_called_once_with(
            'ovsdb-tool', 'join-cluster', '/a/db.file', 'aSchema',
            'ssl:a.b.c.d:1234', 'ssl:e.f.g.h:1234', 'ssl:i.j.k.l:1234')

    def test_configure_tls(self):
        self.patch_object(ovn_central.reactive, 'is_flag_set')
        self.patch_target('get_certs_and_keys')
        self.get_certs_and_keys.return_value = [{
            'cert': 'fakecert',
            'key': 'fakekey',
            'cn': 'fakecn',
            'ca': 'fakeca',
            'chain': 'fakechain',
        }]
        self.patch_object(ovn_central.reactive, 'endpoint_from_name')
        ovsdb_peer = mock.MagicMock()
        ovsdb_peer.db_nb_port = mock.PropertyMock().return_value = 6641
        ovsdb_peer.db_sb_port = mock.PropertyMock().return_value = 6642
        ovsdb_peer.db_sb_admin_port = mock.PropertyMock().return_value = 16642
        ovsdb_peer.cluster_local_addr = mock.PropertyMock().return_value = (
            'a.b.c.d')
        ovsdb = mock.MagicMock()
        ovsdb.db_nb_port = mock.PropertyMock().return_value = 6641
        ovsdb.db_sb_port = mock.PropertyMock().return_value = 6642
        self.endpoint_from_name.side_effect = [ovsdb_peer, ovsdb]
        with mock.patch('builtins.open', create=True) as mocked_open:
            mocked_file = mock.MagicMock(spec=io.FileIO)
            mocked_open.return_value = mocked_file
            self.target.configure_cert = mock.MagicMock()
            self.target.run = mock.MagicMock()
            self.is_flag_set.side_effect = [True, False]
            self.target.configure_tls()
            mocked_open.assert_called_once_with(
                '/etc/openvswitch/ovn-central.crt', 'w')
            mocked_file.__enter__().write.assert_called_once_with(
                'fakeca\nfakechain')
            self.target.configure_cert.assert_called_once_with(
                ovn_central.OVS_ETCDIR,
                'fakecert',
                'fakekey',
                cn='host')
            self.target.run.assert_has_calls([
                mock.call('ovn-nbctl',
                          'set-connection',
                          'pssl:6641'),
                mock.call('ovn-sbctl', '--', '--id=@connection', 'create',
                          'connection', 'role=ovn-controller',
                          'target="pssl:6642"', '--', 'add', 'SB_Global', '.',
                          'connections', '@connection'),
                mock.call('ovn-sbctl', '--', '--id=@connection', 'create',
                          'connection', 'target="pssl:16642:a.b.c.d"', '--',
                          'add', 'SB_Global', '.', 'connections',
                          '@connection'),
            ])

    def test_configure_firewall(self):
        self.patch_object(ovn_central, 'ch_ufw')
        self.ch_ufw.status.return_value = [
            (42, {'from': 'q.r.s.t', 'comment': 'charm-ovn-central'}),
        ]
        self.target.configure_firewall({
            (1, 2, 3, 4,): ('a.b.c.d', 'e.f.g.h',),
            (1, 2,): ('i.j.k.l', 'm.n.o.p',),
        })
        self.ch_ufw.enable.assert_called_once_with()
        self.ch_ufw.modify_access.assert_has_calls([
            mock.call(src=None, dst='any', port=1,
                      proto='tcp', action='reject',
                      comment='charm-ovn-central'),
            mock.call(src=None, dst='any', port=2,
                      proto='tcp', action='reject',
                      comment='charm-ovn-central'),
            mock.call(src=None, dst='any', port=3,
                      proto='tcp', action='reject',
                      comment='charm-ovn-central'),
            mock.call(src=None, dst='any', port=4,
                      proto='tcp', action='reject',
                      comment='charm-ovn-central'),
        ])
        self.ch_ufw.modify_access.assert_has_calls([
            mock.call('a.b.c.d', port=1, proto='tcp', action='allow',
                      prepend=True, comment='charm-ovn-central'),
            mock.call('e.f.g.h', port=1, proto='tcp', action='allow',
                      prepend=True, comment='charm-ovn-central'),
            mock.call('a.b.c.d', port=2, proto='tcp', action='allow',
                      prepend=True, comment='charm-ovn-central'),
            mock.call('e.f.g.h', port=2, proto='tcp', action='allow',
                      prepend=True, comment='charm-ovn-central'),
            mock.call('a.b.c.d', port=3, proto='tcp', action='allow',
                      prepend=True, comment='charm-ovn-central'),
            mock.call('e.f.g.h', port=3, proto='tcp', action='allow',
                      prepend=True, comment='charm-ovn-central'),
            mock.call('a.b.c.d', port=4, proto='tcp', action='allow',
                      prepend=True, comment='charm-ovn-central'),
            mock.call('e.f.g.h', port=4, proto='tcp', action='allow',
                      prepend=True, comment='charm-ovn-central'),
            mock.call('i.j.k.l', port=1, proto='tcp', action='allow',
                      prepend=True, comment='charm-ovn-central'),
            mock.call('m.n.o.p', port=1, proto='tcp', action='allow',
                      prepend=True, comment='charm-ovn-central'),
            mock.call('i.j.k.l', port=2, proto='tcp', action='allow',
                      prepend=True, comment='charm-ovn-central'),
            mock.call('m.n.o.p', port=2, proto='tcp', action='allow',
                      prepend=True, comment='charm-ovn-central'),
        ])
        self.ch_ufw.modify_access.assert_has_calls([
            mock.call(None, dst=None, action='delete', index=42)
        ])
        self.ch_ufw.reset_mock()
        self.target.configure_firewall({
            (1, 2, 3, 4,): ('a.b.c.d', 'e.f.g.h',),
            (1, 2, 5,): None,
        })
        self.ch_ufw.modify_access.assert_has_calls([
            mock.call(src=None, dst='any', port=1,
                      proto='tcp', action='reject',
                      comment='charm-ovn-central'),
            mock.call(src=None, dst='any', port=2,
                      proto='tcp', action='reject',
                      comment='charm-ovn-central'),
            mock.call(src=None, dst='any', port=3,
                      proto='tcp', action='reject',
                      comment='charm-ovn-central'),
            mock.call(src=None, dst='any', port=4,
                      proto='tcp', action='reject',
                      comment='charm-ovn-central'),
            mock.call(src=None, dst='any', port=5,
                      proto='tcp', action='reject',
                      comment='charm-ovn-central'),
        ])
        self.ch_ufw.modify_access.assert_has_calls([
            mock.call('a.b.c.d', port=1, proto='tcp', action='allow',
                      prepend=True, comment='charm-ovn-central'),
            mock.call('e.f.g.h', port=1, proto='tcp', action='allow',
                      prepend=True, comment='charm-ovn-central'),
            mock.call('a.b.c.d', port=2, proto='tcp', action='allow',
                      prepend=True, comment='charm-ovn-central'),
            mock.call('e.f.g.h', port=2, proto='tcp', action='allow',
                      prepend=True, comment='charm-ovn-central'),
            mock.call('a.b.c.d', port=3, proto='tcp', action='allow',
                      prepend=True, comment='charm-ovn-central'),
            mock.call('e.f.g.h', port=3, proto='tcp', action='allow',
                      prepend=True, comment='charm-ovn-central'),
            mock.call('a.b.c.d', port=4, proto='tcp', action='allow',
                      prepend=True, comment='charm-ovn-central'),
            mock.call('e.f.g.h', port=4, proto='tcp', action='allow',
                      prepend=True, comment='charm-ovn-central'),
        ])
        self.ch_ufw.modify_access.assert_has_calls([
            mock.call(None, dst=None, action='delete', index=42)
        ])
