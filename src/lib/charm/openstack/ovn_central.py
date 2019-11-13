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
from charmhelpers.contrib.network import ufw as ch_ufw

import charms_openstack.adapters
import charms_openstack.charm

import charms.ovn as ovn


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
        # NOTE(fnordahl) The OVN central components are currently packaged with
        # a dependency on openvswitch-switch, but it does not need the switch
        # or stock ovsdb running.
        service_masks = [
            'openvswitch-switch.service',
            'ovs-vswitchd.service',
            'ovsdb-server.service',
            'ovn-central.service',
        ]
        for service_file in service_masks:
            abs_path_svc = os.path.join('/etc/systemd/system', service_file)
            if not os.path.islink(abs_path_svc):
                os.symlink('/dev/null', abs_path_svc)
        self.configure_source()
        super().install()

    def _default_port_list(self, *_):
        """Return list of ports the payload listens to.

        The api_ports class attribute can not be used as it does not allow
        one service to listen to multiple ports.

        :returns: port numbers the payload listens to.
        :rtype: List[int]
        """
        # NOTE(fnordahl): the port check  does not appear to cope with
        # ports bound to a specific interface LP: #1843434
        return [6641, 6642]

    def ports_to_check(self, *_):
        """Return list of ports to check the payload listens too.

        The api_ports class attribute can not be used as it does not allow
        one service to listen to multiple ports.

        :returns: ports numbers the payload listens to.
        :rtype List[int]
        """
        return self._default_port_list()

    def custom_assess_status_check(self):
        """Add clustered DB status to status message."""
        db_leader = []
        for db in ('ovnnb_db', 'ovnsb_db',):
            if ovn.is_cluster_leader(db):
                db_leader.append(db)
        if db_leader:
            return ('active',
                    'Unit is ready (leader: {})'
                    .format(', '.join(db_leader)))
        return None, None

    def enable_services(self):
        """Enable services.

        :returns: True on success, False on failure.
        :rtype: bool
        """
        if self.check_if_paused() != (None, None):
            return False
        for service in self.services:
            ch_core.host.service_resume(service)
        return True

    def run(self, *args):
        """Fork off a proc and run commands, collect output and return code.

        :param args: Arguments
        :type args: Union
        :returns: subprocess.CompletedProcess object
        :rtype: subprocess.CompletedProcess
        :raises: subprocess.CalledProcessError
        """
        cp = subprocess.run(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True,
            universal_newlines=True)
        ch_core.hookenv.log(cp, level=ch_core.hookenv.INFO)

    def join_cluster(self, db_file, schema_name, local_conn, remote_conn):
        """Maybe create a OVSDB file with remote peer connection information.

        :param db_file: Full path to OVSDB file
        :type db_file: str
        :param schema_name: OVSDB Schema [OVN_Northbound, OVN_Southbound]
        :type schema_name: str
        :param local_conn: Connection string for local unit
        :type local_conn: Union[str, ...]
        :param remote_conn: Connection string for remote unit(s)
        :type remote_conn: Union[str, ...]
        :raises: subprocess.CalledProcessError
        """
        if os.path.exists(db_file):
            return
        cmd = ['ovsdb-tool', 'join-cluster', db_file, schema_name]
        cmd.extend(list(local_conn))
        cmd.extend(list(remote_conn))
        ch_core.hookenv.log(cmd, level=ch_core.hookenv.INFO)
        self.run(*cmd)

    def configure_tls(self, certificates_interface=None):
        """Override default handler prepare certs per OVNs taste.

        :param certificates_interface: Certificates interface if present
        :type certificates_interface: Optional[reactive.Endpoint]
        :raises: subprocess.CalledProcessError
        """
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
                         'target="pssl:{}:{}"'
                         .format(ovsdb_peer.db_sb_admin_port,
                                 ovsdb_peer.cluster_local_addr), '--',
                         'add', 'SB_Global', '.', 'connections', '@connection')
            self.restart_all()
            break

    def configure_firewall(self, port_addr_map):
        """Configure firewall.

        Lock down access to ports not protected by OVN RBAC.

        :param port_addr_map: Map of ports to addresses to allow.
        :type port_addr_map: Dict[Tuple[int, ...], Optional[Iterator]]
        :param allowed_hosts: Hosts allowed to connect.
        :type allowed_hosts: Iterator
        """
        ufw_comment = 'charm-' + self.name

        # set default allow
        ch_ufw.enable()
        ch_ufw.default_policy('allow', 'incoming')
        ch_ufw.default_policy('allow', 'outgoing')
        ch_ufw.default_policy('allow', 'routed')
        # reject connection to protected ports
        for port in set().union(*port_addr_map.keys()):
            ch_ufw.modify_access(src=None, dst='any', port=port,
                                 proto='tcp', action='reject',
                                 comment=ufw_comment)
        # allow connections from provided addresses
        allowed_addrs = {}
        for ports, addrs in port_addr_map.items():
            # store List copy of addrs to iterate over it multiple times
            _addrs = list(addrs or [])
            for port in ports:
                for addr in _addrs:
                    ch_ufw.modify_access(addr, port=port, proto='tcp',
                                         action='allow', prepend=True,
                                         comment=ufw_comment)
                    allowed_addrs[addr] = 1
        # delete any rules managed by us that do not match provided addresses
        delete_rules = []
        for num, rule in ch_ufw.status():
            if 'comment' in rule and rule['comment'] == ufw_comment:
                if (rule['action'] == 'allow in' and
                        rule['from'] not in allowed_addrs):
                    delete_rules.append(num)
        for rule in sorted(delete_rules, reverse=True):
            ch_ufw.modify_access(None, dst=None, action='delete', index=rule)
