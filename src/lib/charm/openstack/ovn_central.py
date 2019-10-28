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
    # NOTE(fnordahl) we replace the package sysv init script with our own
    # systemd service files.
    #
    # The issue that triggered this change is that to be able to pass the
    # correct command line arguments to ``ovn-nortrhd`` we need to create
    # a ``/etc/openvswitch/ovn-northd-db-params.conf`` which has the side
    # effect of profoundly changing the behaviour of the ``ovn-ctl`` tool
    # that the ``ovn-central`` init script makes use of.
    #
    # https://github.com/ovn-org/ovn/blob/dc0e10c068c20c4e59c9c86ecee26baf8ed50e90/utilities/ovn-ctl#L323
    #
    # TODO: The systemd service files should be upstreamed and removed from
    # the charm
    restart_map = {
        '/etc/default/ovn-central': services,
        os.path.join(OVS_ETCDIR, 'ovn-northd-db-params.conf'): ['ovn-northd'],
        '/lib/systemd/system/ovn-central.service': [],
        '/lib/systemd/system/ovn-northd.service': [],
        '/lib/systemd/system/ovn-nb-ovsdb.service': [],
        '/lib/systemd/system/ovn-sb-ovsdb.service': [],
    }
    python_version = 3
    source_config_key = 'source'

    def install(self):
        """Extend the default install method.

        Mask services before initial installation.

        This is done to prevent extraneous standalone DB initialization and
        subsequent upgrade to clustered DB when configuration is rendered.

        We need to manually create the symlink as the package is not installed
        yet and subsequently systemctl(1) has no knowledge of it.

        We also configure source before installing as OpenvSwitch and OVN
        packages are distributed as part of the UCA.
        """
        service_masks = [
            '/etc/systemd/system/ovn-central.service',
        ]
        for service_file in service_masks:
            if not os.path.islink(service_file):
                os.symlink('/dev/null', service_file)
        self.configure_source()
        super().install()

    def _default_port_list(self, *_):
        """Return list of ports the payload listens too.

        The api_ports class attribute can not be used as it does not allow
        one service to listen to multiple ports.
        """
        # NOTE(fnordahl): the port check  does not appear to cope with
        # ports bound to a specific interface LP: #1843434
        return [6641, 6642]

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
                crt.write(
                    tls_object['ca'] +
                    os.linesep +
                    tls_object.get('chain', ''))
            self.configure_cert(OVS_ETCDIR,
                                tls_object['cert'],
                                tls_object['key'],
                                cn='host')
            self.run('ovs-vsctl',
                     'set-ssl',
                     ovn_key(self.adapters_instance),
                     ovn_cert(self.adapters_instance),
                     ovn_ca_cert(self.adapters_instance))
            if (reactive.is_flag_set('leadership.is_leader') and not
                    reactive.is_flag_set('leadership.set.ready')):
                # This is one-time set up at cluster creation and can only be
                # done one the OVSDB cluster leader.
                #
                # It also has to be done after certificates has been written
                # to disk and before we do anything else which is why it is
                # co-located with the ``configure_tls`` method.
                #
                # NOTE: There is one individual OVSDB cluster leader for each
                # of the OVSDB databases and throughout a deployment lifetime
                # they are not necessarilly the same as the charm leader.
                #
                # However, at bootstrap time the OVSDB cluster leaders will
                # coincide with the charm leader.
                ovsdb_peer = reactive.endpoint_from_name('ovsdb-peer')
                ovsdb_client = reactive.endpoint_from_name('ovsdb')
                self.run('ovn-nbctl',
                         'set-connection',
                         'pssl:{}'.format(ovsdb_peer.db_nb_port))
                self.run('ovn-sbctl',
                         '--',
                         '--id=@connection',
                         'create', 'connection', 'role=ovn-controller',
                         'target="pssl:{}"'
                         .format(ovsdb_client.db_sb_port), '--',
                         'add', 'SB_Global', '.', 'connections', '@connection')
                self.run('ovn-sbctl',
                         '--',
                         '--id=@connection',
                         'create', 'connection',
                         'target="pssl:{}"'
                         .format(ovsdb_peer.db_sb_admin_port,
                                 ovsdb_peer.cluster_local_addr), '--',
                         'add', 'SB_Global', '.', 'connections', '@connection')
            self.restart_all()
            break

    def configure_ovn_remote(self, ovsdb_interface):
        """Configure the OVN remote setting in the local OVSDB.

        The value is used by command line tools run on this unit.

        :param ovsdb_interface: OVSDB interface instance
        :type ovsdb_interface: reactive.Endpoint derived class
        :raises: subprocess.CalledProcessError
        """
        self.run('ovs-vsctl',
                 'set',
                 'open',
                 '.',
                 'external-ids:ovn-remote={}'
                 .format(','.join(ovsdb_interface.db_sb_connection_strs)))
