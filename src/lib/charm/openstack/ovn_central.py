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
import os
import subprocess

import charms.reactive as reactive

import charmhelpers.core as ch_core

import charms_openstack.adapters
import charms_openstack.charm


OVS_ETCDIR = '/etc/openvswitch'
# XXX get these from the ovsdb-cluster interface
DB_NB_PORT = 6641
DB_SB_PORT = 6642
DB_NB_CLUSTER_PORT = 6643
DB_SB_CLUSTER_PORT = 6644


@charms_openstack.adapters.config_property
def ovn_key(cls):
    return os.path.join(OVS_ETCDIR, 'key_host')


@charms_openstack.adapters.config_property
def ovn_cert(cls):
    return os.path.join(OVS_ETCDIR, 'cert_host')


@charms_openstack.adapters.config_property
def ovn_ca_cert(cls):
    return os.path.join(OVS_ETCDIR,
                        '{}.crt'.format(cls.charm_instance.name))


class OVNCentralCharm(charms_openstack.charm.OpenStackCharm):
    # OpenvSwitch and OVN is distributed as part of the Ubuntu Cloud Archive
    # Pockets get their name from OpenStack releases
    release = 'train'
    package_codenames = {
        'ovn-central': collections.OrderedDict([
            ('2.12', 'train'),
        ]),
    }
    name = 'ovn-central'
    packages = ['ovn-central']
    services = ['ovn-central']
    required_relations = ['certificates']
    restart_map = {
        '/etc/default/ovn-central': services,
    }
    python_version = 3
    source_config_key = 'source'

    def install(self):
        """Extend the default install method.

        Mask the ``ovn-central`` service before initial installation.

        This is done to prevent extraneous standalone DB initialization and
        subsequent upgrade to clustered DB when configuration is rendered.

        We need to manually create the symlink as the package is not installed
        yet and subsequently systemctl(1) has no knowledge of it.

        We also configure source before installing as OpenvSwitch and OVN
        packages are distributed as part of the UCA.
        """
        ovn_central_service_file = '/etc/systemd/system/ovn-central.service'
        if not os.path.islink(ovn_central_service_file):
            os.symlink('/dev/null', ovn_central_service_file)
        self.configure_source()
        super().install()

    def _default_port_list(self, *_):
        """Return list of ports the payload listens too.

        The api_ports class attribute can not be used as it does not allow
        one service to listen to multiple ports.
        """
        # NOTE(fnordahl): the port check  does not appear to cope with
        # ports bound to a specific interface LP: #1843434
        return [DB_NB_PORT, DB_SB_PORT]

    def ports_to_check(self, *_):
        """Return list of ports to check the payload listens too.

        The api_ports class attribute can not be used as it does not allow
        one service to listen to multiple ports.
        """
        return self._default_port_list()

    def enable_services(self):
        """Enable services.

        :returns: True on success, False on failure.
        :rtype: bool"""
        if self.check_if_paused() != (None, None):
            return False
        for service in self.services:
            ch_core.host.service_resume(service)
        return True

    def run(self, *args):
        cp = subprocess.run(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True,
            universal_newlines=True)
        ch_core.hookenv.log(cp, level=ch_core.hookenv.INFO)

    def join_cluster(self, db_file, schema_name, local_conn, remote_conn):
        if os.path.exists(db_file):
            return
        cmd = ['ovsdb-tool', 'join-cluster', db_file, schema_name]
        cmd.extend(list(local_conn))
        cmd.extend(list(remote_conn))
        ch_core.hookenv.log(cmd, level=ch_core.hookenv.INFO)
        self.run(*cmd)

    def configure_tls(self, certificates_interface=None):
        """Override default handler prepare certs per OVNs taste."""
        tls_objects = self.get_certs_and_keys(
            certificates_interface=certificates_interface)

        for tls_object in tls_objects:
            with open(ovn_ca_cert(self.adapters_instance), 'w') as crt:
                chain = tls_object.get('chain')
                if chain:
                    crt.write(tls_object['ca'] + os.linesep + chain)
                else:
                    crt.write(tls_object['ca'])

            self.configure_cert(OVS_ETCDIR,
                                tls_object['cert'],
                                tls_object['key'],
                                cn='host')
            self.run('ovs-vsctl',
                     'set-ssl',
                     ovn_key(self.adapters_instance),
                     ovn_cert(self.adapters_instance),
                     ovn_ca_cert(self.adapters_instance))
            self.run('ovs-vsctl',
                     'set',
                     'open',
                     '.',
                     'external-ids:ovn-remote=ssl:127.0.0.1:{}'
                     .format(DB_SB_PORT))
            if reactive.is_flag_set('leadership.is_leader'):
                self.run('ovn-nbctl',
                         'set-connection',
                         'pssl:{}'.format(DB_NB_PORT))
                # NOTE(fnordahl): Temporarilly disable RBAC, we need to figure
                #                 out how to pre-populate the Chassis database
                #                 before enabling this.
                # self.run('ovn-sbctl',
                #          'set-connection',
                #          'role=ovn-controller',
                #          'pssl:{}'.format(DB_SB_PORT))
                self.run('ovn-sbctl',
                         'set-connection',
                         'pssl:{}'.format(DB_SB_PORT))
                self.restart_all()
            break
