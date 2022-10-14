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

import collections
import io
import unittest.mock as mock

import charms_openstack.test_utils as test_utils

import charm.openstack.ovn_central as ovn_central


class Helper(test_utils.PatchHelper):

    def setUp(self):
        super().setUp()
        self.patch_release(ovn_central.UssuriOVNCentralCharm.release)
        self.patch_object(
            ovn_central.charms_openstack.adapters, 'config_property')
        self.target = ovn_central.UssuriOVNCentralCharm()

    def patch_target(self, attr, return_value=None):
        mocked = mock.patch.object(self.target, attr)
        self._patches[attr] = mocked
        started = mocked.start()
        started.return_value = return_value
        self._patches_start[attr] = started
        setattr(self, attr, started)


class TestOVNCentralConfigurationAdapter(test_utils.PatchHelper):

    def setUp(self):
        super().setUp()
        self.charm_instance = mock.MagicMock()
        self.charm_instance.ovn_sysconfdir.return_value = '/etc/path'
        self.target = ovn_central.OVNCentralConfigurationAdapter(
            charm_instance=self.charm_instance)

    def test__ovn_source(self):
        self.patch_object(ovn_central.reactive, 'is_flag_set',
                          return_value=True)
        self.patch_object(ovn_central.ch_core.host, 'lsb_release',
                          return_value={'DISTRIB_CODENAME': 'focal'})
        # User has supplied a ovn-source config
        m = mock.patch.object(ovn_central.ch_core.hookenv, 'config',
                              return_value={'ovn-source': 'fake-source'})
        m.start()
        self.target = ovn_central.OVNCentralConfigurationAdapter(
            charm_instance=self.charm_instance)
        m.stop()
        setattr(self, 'config', None)
        self.assertEquals('fake-source', self.target._ovn_source)

        # User has not supplied a ovn-source config, charm was installed at
        # this version on focal
        m = mock.patch.object(ovn_central.ch_core.hookenv, 'config',
                              return_value={'ovn-source': ''})
        m.start()
        self.target = ovn_central.OVNCentralConfigurationAdapter(
            charm_instance=self.charm_instance)
        m.stop()
        setattr(self, 'config', None)
        self.assertEquals('cloud:focal-ovn-22.03', self.target._ovn_source)

        # User has not supplied a ovn-source config, charm was upgraded
        self.is_flag_set.return_value = False
        self.assertEquals('', self.target._ovn_source)

        # User has not supplied a ovn-source config, charm was installed at
        # this version on jammy
        self.is_flag_set.return_value = True
        self.lsb_release.return_value = {'DISTRIB_CODENAME': 'jammy'}
        self.assertEquals('', self.target._ovn_source)


class TestOVNCentralCharm(Helper):

    class FakeClusterStatus(object):

        def __init__(self, is_cluster_leader=None):
            self.is_cluster_leader = is_cluster_leader

    def test_install_train(self):
        self.patch_release(ovn_central.TrainOVNCentralCharm.release)
        self.patch_object(ovn_central.ch_core.hookenv, 'config',
                          return_value={'ovn-source': ''})
        self.target = ovn_central.TrainOVNCentralCharm()
        self.patch_object(ovn_central.charms_openstack.charm.OpenStackCharm,
                          'install')
        self.patch_object(ovn_central.os.path, 'islink')
        self.islink.return_value = False
        self.patch_object(ovn_central.os, 'symlink')
        self.patch_target('configure_sources')
        self.patch_object(ovn_central.os, 'mkdir')
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

    def test_install(self):
        self.patch_object(ovn_central.charms_openstack.charm.OpenStackCharm,
                          'install')
        self.patch_object(ovn_central.os.path, 'islink')
        self.islink.return_value = False
        self.patch_object(ovn_central.os, 'symlink')
        self.patch_target('configure_sources')
        self.patch_object(ovn_central.os, 'mkdir')
        self.target.install()
        calls = []
        for service in (self.target.services[0],
                        'ovn-ovsdb-server-nb',
                        'ovn-ovsdb-server-sb',):
            calls.append(
                mock.call('/etc/systemd/system/{}.service'.format(service)))
        self.islink.assert_has_calls(calls)
        calls = []
        for service in (self.target.services[0], 'ovn-ovsdb-server-nb',
                        'ovn-ovsdb-server-sb',):
            calls.append(
                mock.call('/dev/null',
                          '/etc/systemd/system/{}.service'.format(service)))
        self.symlink.assert_has_calls(calls)
        self.install.assert_called_once_with()
        self.configure_sources.assert_called_once_with()

    def test_configure_ovn_source(self):
        self.patch_target('configure_source')
        self.patch_object(ovn_central.ch_core.hookenv, 'config',
                          return_value={'source': 'fake-source',
                                        'ovn-source': ''})
        self.patch_object(ovn_central.OVNCentralConfigurationAdapter,
                          '_ovn_source',
                          new=mock.PropertyMock())
        self._ovn_source.return_value = 'cloud:focal-ovn-22.03'
        self.patch_object(ovn_central.ch_fetch, 'add_source')
        self.patch_object(ovn_central.ch_fetch, 'apt_update')
        self.target.configure_ovn_source()
        self.add_source.assert_called_once_with('cloud:focal-ovn-22.03')
        self.assertFalse(self.configure_source.called)

    def test_states_to_check(self):
        self.maxDiff = None
        expect = collections.OrderedDict([
            ('ovsdb-peer', [
                ('ovsdb-peer.connected',
                 'blocked',
                 'Charm requires peers to operate, add more units. A minimum '
                 'of 3 is required for HA'),
                ('ovsdb-peer.available',
                 'waiting',
                 "'ovsdb-peer' incomplete")]),
            ('certificates', [
                ('certificates.available', 'blocked',
                 "'certificates' missing"),
                ('certificates.server.certs.available',
                 'waiting',
                 "'certificates' awaiting server certificate data")]),
        ])
        self.assertDictEqual(self.target.states_to_check(), expect)

    def test__default_port_list(self):
        self.assertEquals(
            self.target._default_port_list(),
            [6641, 6642])

    def test_ports_to_check(self):
        self.target._default_port_list = mock.MagicMock()
        self.target.ports_to_check()
        self.target._default_port_list.assert_called_once_with()

    def test_cluster_status_mesage(self):
        self.patch_target('cluster_status')
        self.patch_target('is_northd_active')
        self.cluster_status.side_effect = [
            self.FakeClusterStatus(False),
            self.FakeClusterStatus(False),
        ]
        self.is_northd_active.return_value = False
        self.assertEquals(
            self.target.cluster_status_message(), '')
        self.cluster_status.assert_has_calls([
            mock.call('ovnnb_db'),
            mock.call('ovnsb_db'),
        ])
        self.cluster_status.side_effect = [
            self.FakeClusterStatus(True),
            self.FakeClusterStatus(False),
        ]
        self.assertEquals(
            self.target.cluster_status_message(),
            'leader: ovnnb_db')
        self.cluster_status.side_effect = [
            self.FakeClusterStatus(True),
            self.FakeClusterStatus(True),
        ]
        self.assertEquals(
            self.target.cluster_status_message(),
            'leader: ovnnb_db, ovnsb_db')
        self.cluster_status.side_effect = [
            self.FakeClusterStatus(False),
            self.FakeClusterStatus(False),
        ]
        self.is_northd_active.return_value = True
        self.assertEquals(
            self.target.cluster_status_message(),
            'northd: active')
        self.cluster_status.side_effect = [
            self.FakeClusterStatus(True),
            self.FakeClusterStatus(False),
        ]
        self.assertEquals(
            self.target.cluster_status_message(),
            'leader: ovnnb_db northd: active')
        self.cluster_status.side_effect = [
            self.FakeClusterStatus(True),
            self.FakeClusterStatus(True),
        ]
        self.assertEquals(
            self.target.cluster_status_message(),
            'leader: ovnnb_db, ovnsb_db northd: active')

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
        self.patch_target('get_certs_and_keys')
        self.get_certs_and_keys.return_value = [{
            'cert': 'fakecert',
            'key': 'fakekey',
            'cn': 'fakecn',
            'ca': 'fakeca',
            'chain': 'fakechain',
        }]
        with mock.patch('builtins.open', create=True) as mocked_open:
            mocked_file = mock.MagicMock(spec=io.FileIO)
            mocked_open.return_value = mocked_file
            self.target.configure_cert = mock.MagicMock()
            self.target.configure_tls()
            mocked_open.assert_called_once_with(
                '/etc/ovn/ovn-central.crt', 'w')
            mocked_file.__enter__().write.assert_called_once_with(
                'fakeca\nfakechain')
            self.target.configure_cert.assert_called_once_with(
                '/etc/ovn',
                'fakecert',
                'fakekey',
                cn='host')

    def test_configure_ovn_listener(self):
        self.patch_object(ovn_central.ch_ovsdb, 'SimpleOVSDB')
        self.patch_target('run')
        port_map = {6641: {'inactivity_probe': 42},
                    6642: {'role': 'ovn-controller'}}
        self.patch_target('cluster_status')

        cluster_status = self.FakeClusterStatus()
        self.cluster_status.return_value = cluster_status
        cluster_status.is_cluster_leader = False
        self.target.configure_ovn_listener('nb', port_map)
        self.assertFalse(self.SimpleOVSDB.called)
        cluster_status.is_cluster_leader = True
        ovsdb = mock.MagicMock()
        ovsdb.connection.find.side_effect = [
            [],
            [{'_uuid': 'fake-uuid'}],
            [],
            [{'_uuid': 'fake-uuid'}],
        ]
        self.SimpleOVSDB.return_value = ovsdb
        self.target.configure_ovn_listener('nb', port_map)
        self.run.assert_has_calls([
            mock.call('ovn-nbctl', '--', '--id=@connection', 'create',
                      'connection', 'target="pssl:6641"', '--', 'add',
                      'NB_Global', '.', 'connections', '@connection'),
            mock.call('ovn-nbctl', '--', '--id=@connection', 'create',
                      'connection', 'target="pssl:6642"', '--', 'add',
                      'NB_Global', '.', 'connections', '@connection'),
        ])
        ovsdb.connection.set.assert_has_calls([
            mock.call('fake-uuid', 'inactivity_probe', 42),
            mock.call('fake-uuid', 'role', 'ovn-controller')
        ])

    def test_validate_config(self):
        self.patch_target('config')
        self.config.__getitem__.return_value = self.target.min_election_timer
        self.assertEquals(self.target.validate_config(), (None, None))
        self.config.__getitem__.return_value = self.target.max_election_timer
        self.assertEquals(self.target.validate_config(), (None, None))
        self.config.__getitem__.return_value = (
            self.target.min_election_timer - 1)
        self.assertEquals(self.target.validate_config(), ('blocked', mock.ANY))
        self.config.__getitem__.return_value = (
            self.target.max_election_timer + 1)
        self.assertEquals(self.target.validate_config(), ('blocked', mock.ANY))

    def test_configure_ovsdb_election_timer(self):
        with self.assertRaises(ValueError):
            self.target.configure_ovsdb_election_timer('aDb', 42)
        self.patch_target('cluster_status')
        self.patch_object(ovn_central.time, 'sleep')

        _election_timer = 1000

        class FakeClusterStatus(object):

            def __init__(self):
                self.is_cluster_leader = True

            @property
            def election_timer(self):
                nonlocal _election_timer
                return _election_timer

        def fake_ovn_appctl(db, cmd, **kwargs):
            nonlocal _election_timer
            _election_timer = int(cmd[2])

        cluster_status = FakeClusterStatus()
        self.cluster_status.return_value = cluster_status
        self.patch_object(ovn_central.ch_ovn, 'ovn_appctl')
        self.ovn_appctl.side_effect = fake_ovn_appctl
        self.target.configure_ovsdb_election_timer('sb', 42)
        self.ovn_appctl.assert_has_calls([
            mock.call(
                'ovnsb_db',
                ('cluster/change-election-timer', 'OVN_Southbound', '2000'),
                rundir='/var/run/ovn',
                use_ovs_appctl=False),
            mock.call(
                'ovnsb_db',
                ('cluster/change-election-timer', 'OVN_Southbound', '4000'),
                rundir='/var/run/ovn',
                use_ovs_appctl=False),
            mock.call(
                'ovnsb_db',
                ('cluster/change-election-timer', 'OVN_Southbound', '8000'),
                rundir='/var/run/ovn',
                use_ovs_appctl=False),
            mock.call(
                'ovnsb_db',
                ('cluster/change-election-timer', 'OVN_Southbound', '16000'),
                rundir='/var/run/ovn',
                use_ovs_appctl=False),
            mock.call(
                'ovnsb_db',
                ('cluster/change-election-timer', 'OVN_Southbound', '32000'),
                rundir='/var/run/ovn',
                use_ovs_appctl=False),
            mock.call(
                'ovnsb_db',
                ('cluster/change-election-timer', 'OVN_Southbound', '42000'),
                rundir='/var/run/ovn',
                use_ovs_appctl=False)
        ])
        _election_timer = 42000
        self.ovn_appctl.reset_mock()
        self.target.configure_ovsdb_election_timer('sb', 1)
        self.ovn_appctl.assert_has_calls([
            mock.call(
                'ovnsb_db',
                ('cluster/change-election-timer', 'OVN_Southbound', '21000'),
                rundir='/var/run/ovn',
                use_ovs_appctl=False),
            mock.call(
                'ovnsb_db',
                ('cluster/change-election-timer', 'OVN_Southbound', '10500'),
                rundir='/var/run/ovn',
                use_ovs_appctl=False),
            mock.call(
                'ovnsb_db',
                ('cluster/change-election-timer', 'OVN_Southbound', '5250'),
                rundir='/var/run/ovn',
                use_ovs_appctl=False),
            mock.call(
                'ovnsb_db',
                ('cluster/change-election-timer', 'OVN_Southbound', '2625'),
                rundir='/var/run/ovn',
                use_ovs_appctl=False),
            mock.call(
                'ovnsb_db',
                ('cluster/change-election-timer', 'OVN_Southbound', '1312'),
                rundir='/var/run/ovn',
                use_ovs_appctl=False),
            mock.call(
                'ovnsb_db',
                ('cluster/change-election-timer', 'OVN_Southbound', '1000'),
                rundir='/var/run/ovn',
                use_ovs_appctl=False),
        ])

    def test_configure_ovn(self):
        self.patch_target('config')
        self.config.__getitem__.return_value = 42
        self.patch_target('configure_ovn_listener')
        self.patch_target('configure_ovsdb_election_timer')
        self.target.configure_ovn(1, 2, 3)
        self.config.__getitem__.assert_has_calls([
            mock.call('ovsdb-server-inactivity-probe'),
            mock.call('ovsdb-server-election-timer'),
        ])
        self.configure_ovn_listener.assert_has_calls([
            mock.call('nb', {1: {'inactivity_probe': 42000}}),
            mock.call('sb', {2: {'role': 'ovn-controller',
                                 'inactivity_probe': 42000}}),
            mock.call('sb', {3: {'inactivity_probe': 42000}}),
        ])
        self.configure_ovsdb_election_timer.assert_has_calls([
            mock.call('nb', 42),
            mock.call('sb', 42),
        ])

    def test_initialize_firewall(self):
        self.patch_object(ovn_central, 'ch_ufw')
        self.target.initialize_firewall()
        self.ch_ufw.enable.assert_called_once_with()
        self.ch_ufw.default_policy.assert_has_calls([
            mock.call('allow', 'incoming'),
            mock.call('allow', 'outgoing'),
            mock.call('allow', 'routed'),
        ])

    def test_configure_firewall(self):
        self.patch_object(ovn_central, 'ch_ufw')
        self.ch_ufw.status.return_value = [
            (42, {
                'action': 'allow in',
                'from': 'q.r.s.t',
                'comment': 'charm-ovn-central'}),
            (51, {
                'action': 'reject in',
                'from': 'any',
                'comment': 'charm-ovn-central'}),
        ]
        self.target.configure_firewall({
            (1, 2, 3, 4,): ('a.b.c.d', 'e.f.g.h',),
            (1, 2,): ('i.j.k.l', 'm.n.o.p',),
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
        ], any_order=True)
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
        ], any_order=True)
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
        ], any_order=True)
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
        ], any_order=True)
        self.ch_ufw.modify_access.assert_has_calls([
            mock.call(None, dst=None, action='delete', index=42)
        ])

    def test_render_nrpe(self):
        self.patch_object(ovn_central.nrpe, 'NRPE')
        self.patch_object(ovn_central.nrpe, 'add_init_service_checks')
        self.target.render_nrpe()
        # Note that this list is valid for Ussuri
        self.add_init_service_checks.assert_has_calls([
            mock.call().add_init_service_checks(
                mock.ANY,
                ['ovn-northd', 'ovn-ovsdb-server-nb', 'ovn-ovsdb-server-sb'],
                mock.ANY
            ),
        ])
        self.NRPE.assert_has_calls([
            mock.call().write(),
        ])

    def test_configure_deferred_restarts(self):
        self.patch_object(
            ovn_central.ch_core.hookenv,
            'config',
            return_value={'enable-auto-restarts': True})
        self.patch_object(
            ovn_central.ch_core.hookenv,
            'service_name',
            return_value='myapp')
        self.patch_object(
            ovn_central.deferred_events,
            'configure_deferred_restarts')
        self.patch_object(ovn_central.os, 'chmod')
        self.target.configure_deferred_restarts()
        self.configure_deferred_restarts.assert_called_once_with(
            ['ovn-central', 'ovn-ovsdb-server-nb', 'ovn-northd',
             'ovn-ovsdb-server-sb'])

        self.chmod.assert_called_once_with(
            '/var/lib/charm/myapp/policy-rc.d',
            493)

    def test_configure_deferred_restarts_unsupported(self):
        self.patch_object(
            ovn_central.ch_core.hookenv,
            'config',
            return_value={})
        self.patch_object(
            ovn_central.deferred_events,
            'configure_deferred_restarts')
        self.target.configure_deferred_restarts()
        self.assertFalse(self.configure_deferred_restarts.called)

    def test_assess_exporter_no_channel_installed(self):
        self.patch_object(
            ovn_central.ch_core.hookenv,
            'config',
            return_value={'ovn-exporter-channel': ''})

        self.patch_object(ovn_central.snap, 'is_installed')
        self.patch_object(ovn_central.snap, 'install')
        self.patch_object(ovn_central.snap, 'remove')
        self.patch_object(ovn_central.snap, 'refresh')

        self.is_installed.return_value = True

        self.target.assess_exporter()
        self.remove.assert_called_once_with('prometheus-ovn-exporter')
        self.install.assert_not_called()
        self.refresh.assert_not_called()

    def test_assess_exporter_no_channel_not_installed(self):
        self.patch_object(
            ovn_central.ch_core.hookenv,
            'config',
            return_value={'ovn-exporter-channel': ''})

        self.patch_object(ovn_central.snap, 'is_installed')
        self.patch_object(ovn_central.snap, 'install')
        self.patch_object(ovn_central.snap, 'remove')
        self.patch_object(ovn_central.snap, 'refresh')

        self.is_installed.return_value = False

        self.target.assess_exporter()
        self.install.assert_not_called()
        self.refresh.assert_not_called()
        self.remove.assert_not_called()

    def test_assess_exporter_fresh_install(self):
        self.patch_object(
            ovn_central.ch_core.hookenv,
            'config',
            return_value={'ovn-exporter-channel': 'stable'})
        self.patch_object(ovn_central.snap, 'is_installed')
        self.patch_object(ovn_central.snap, 'install')
        self.patch_object(ovn_central.snap, 'remove')
        self.patch_object(ovn_central.snap, 'refresh')

        self.is_installed.return_value = False

        self.target.assess_exporter()

        self.install.assert_called_once_with(
            'prometheus-ovn-exporter',
            channel='stable',
            devmode=True)
        self.remove.assert_not_called()
        self.refresh.assert_not_called()

    def test_assess_exporter_refresh(self):
        self.patch_object(
            ovn_central.ch_core.hookenv,
            'config',
            return_value={'ovn-exporter-channel': 'stable'})

        self.patch_object(ovn_central.snap, 'is_installed')
        self.patch_object(ovn_central.snap, 'install')
        self.patch_object(ovn_central.snap, 'remove')
        self.patch_object(ovn_central.snap, 'refresh')

        self.is_installed.return_value = True

        self.target.assess_exporter()

        self.refresh.assert_called_once_with(
            'prometheus-ovn-exporter',
            channel='stable',
            devmode=True)
        self.install.assert_not_called()
        self.remove.assert_not_called()
