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
import operator
import os
import subprocess
import time

import charmhelpers.core as ch_core
from charmhelpers.core.host import rsync, write_file
import charmhelpers.contrib.charmsupport.nrpe as nrpe
import charmhelpers.contrib.network.ovs.ovn as ch_ovn
import charmhelpers.contrib.network.ovs.ovsdb as ch_ovsdb
from charmhelpers.contrib.network import ufw as ch_ufw
import charmhelpers.contrib.openstack.deferred_events as deferred_events
import charmhelpers.contrib.hahelpers.cluster as ch_cluster
import charmhelpers.contrib.openstack.utils as os_utils
import charmhelpers.fetch as ch_fetch

import charms.reactive as reactive

import charms_openstack.adapters
import charms_openstack.charm

from charms.layer import snap

# Release selection need to happen here for correct determination during
# bus discovery and action exection
charms_openstack.charm.use_defaults('charm.default-select-release')

NAGIOS_PLUGINS = '/usr/local/lib/nagios/plugins'
SCRIPTS_DIR = '/usr/local/bin'
CERTCHECK_CRONFILE = '/etc/cron.d/ovn-central-cert-checks'
CRONJOB_CMD = "{schedule} root {command} 2>&1 | logger -p local0.notice\n"

PEER_RELATION = 'ovsdb-peer'
CERT_RELATION = 'certificates'


# NOTE(fnordahl): We should split the ``OVNConfigurationAdapter`` in
# ``layer-ovn`` into common and chassis specific parts so we can re-use the
# common parts here.
class OVNCentralConfigurationAdapter(
        charms_openstack.adapters.ConfigurationAdapter):
    """Provide a configuration adapter for OVN Central."""

    @property
    def ovn_key(self):
        return os.path.join(self.charm_instance.ovn_sysconfdir(), 'key_host')

    @property
    def ovn_cert(self):
        return os.path.join(self.charm_instance.ovn_sysconfdir(), 'cert_host')

    @property
    def ovn_ca_cert(self):
        return os.path.join(self.charm_instance.ovn_sysconfdir(),
                            '{}.crt'.format(self.charm_instance.name))

    @property
    def is_charm_leader(self):
        return reactive.is_flag_set('leadership.is_leader')

    @property
    def _ovn_source(self):
        if (not self.ovn_source
                and reactive.is_flag_set('leadership.set.install_stamp')
                and ch_core.host.lsb_release()['DISTRIB_CODENAME'] == 'focal'):
            return 'cloud:focal-ovn-22.03'
        return self.ovn_source

    @property
    def ovn_exporter_snap_channel(self):
        """Validate a provided snap channel and return it

        Any prefix is ignored ('0.10' in '0.10/stable' for example). If
        a config value is empty it means that the snap does not need to
        be installed.
        """
        channel = self.ovn_exporter_channel
        if not channel:
            return None

        channel_suffix = channel.split('/')[-1]
        if channel_suffix not in ('stable', 'candidate', 'beta', 'edge'):
            return 'stable'
        return channel_suffix


class BaseOVNCentralCharm(charms_openstack.charm.OpenStackCharm):
    abstract_class = True
    # Note that we currently do not support pivoting between release specific
    # charm classes in the OVN charms.  We still need this set to ensure the
    # default methods are happy.
    #
    # Also see docstring in the `upgrade_if_available` method.
    package_codenames = {
        'ovn-central': collections.OrderedDict([
            ('2', 'train'),
            ('20', 'ussuri'),
        ]),
    }
    name = 'ovn-central'
    packages = ['ovn-central']
    services = ['ovn-central']
    nrpe_check_services = []
    release_pkg = 'ovn-central'
    configuration_class = OVNCentralConfigurationAdapter
    required_relations = [PEER_RELATION, CERT_RELATION]
    python_version = 3
    source_config_key = 'source'
    min_election_timer = 1
    max_election_timer = 60

    def __init__(self, **kwargs):
        """Override class init to populate restart map with instance method."""
        self.restart_map = {
            '/etc/default/ovn-central': self.services,
            os.path.join(self.ovn_sysconfdir(),
                         'ovn-northd-db-params.conf'): ['ovn-northd'],
        }
        super().__init__(**kwargs)

    def restart_on_change(self):
        """Restart the services in the self.restart_map{} attribute if any of
        the files identified by the keys changes for the wrapped call.

        Usage:

           with restart_on_change(restart_map, ...):
               do_stuff_that_might_trigger_a_restart()
               ...
        """
        return ch_core.host.restart_on_change(
            self.full_restart_map,
            stopstart=True,
            restart_functions=getattr(self, 'restart_functions', None),
            can_restart_now_f=deferred_events.check_and_record_restart_request,
            post_svc_restart_f=deferred_events.process_svc_restart)

    @property
    def deferable_services(self):
        """Services which should be stopped from restarting.

        All services from self.services are deferable. But the charm may
        install a package which install a service that the charm does not add
        to its restart_map. In that case it will be missing from
        self.services. However one of the jobs of deferred events is to ensure
        that packages updates outside of charms also do not restart services.
        To ensure there is a complete list take the services from self.services
        and also add in a known list of networking services.

        NOTE: It does not matter if one of the services in the list is not
        installed on the system.
        """
        svcs = self.services[:]
        svcs.extend(['ovn-ovsdb-server-nb', 'ovn-ovsdb-server-nb',
                     'ovn-northd', 'ovn-central'])
        return list(set(svcs))

    def configure_ovn_source(self):
        """Configure the OVN overlay archive."""
        if self.options.ovn_source:
            # The end user has added configuration which may require full
            # processing including key extraction.
            self.configure_source(config_key='ovn-source')
        elif self.options._ovn_source:
            # The end user has not added configuration and we want to use the
            # runtime determined default value.
            #
            # We cannot use the default `configure_source` method here as it
            # attempts to access charm config directly.
            ch_fetch.add_source(self.options._ovn_source)
            ch_fetch.apt_update(fatal=True)

    def configure_sources(self):
        """Configure package sources for OVN charms.

        The principal charms provide both a `source` and a `ovn-source`
        configuration option, and the subordinate charms only provide the
        `ovn-source` configuration option.

        The `source` configuration option is tied into the charms.openstack
        `source_config_key` class variable and is inteded to be used with a
        full UCA archive.  The default methods and functions will apply special
        meaning to the name used for further processing, and as such the
        `source` configuration option is not suitable for use with an overlay
        archive.

        The `ovn-source` configuration option is intended to be used with a
        slim overlay archive containing only OVN and its dependencies.

        The two configuration options can be used simultaneously, and the
        underlying charm-helpers code will write the configuration out into
        separate files depending on the value of the options.

        Ref: https://github.com/juju/charm-helpers/commit/982319b136b
        """
        self.configure_ovn_source()
        if self.source_config_key:
            self.configure_source()

    def install(self, service_masks=None):
        """Extend the default install method.

        Mask services before initial installation.

        This is done to prevent extraneous standalone DB initialization and
        subsequent upgrade to clustered DB when configuration is rendered.

        We need to manually create the symlink as the package is not installed
        yet and subsequently systemctl(1) has no knowledge of it.

        We also configure source before installing as OpenvSwitch and OVN
        packages are distributed as part of the UCA.
        """
        # NOTE(fnordahl): The actual masks are provided by the release specific
        # classes.
        service_masks = service_masks or []
        for service_file in service_masks:
            abs_path_svc = os.path.join('/etc/systemd/system', service_file)
            if not os.path.islink(abs_path_svc):
                os.symlink('/dev/null', abs_path_svc)
        self.configure_sources()
        super().install()

    def upgrade_charm(self):
        """Extend the default upgrade_charm method."""
        super().upgrade_charm()

        # Ensure that `config.changed.ovn-source` flag is not set on charm
        # upgrade.  When upgrading from an older charm, this flag will be
        # set even though the config has not changed.
        reactive.clear_flag('config.changed.ovn-source')

    def ovn_upgrade_available(self, package=None, snap=None):
        """Determine whether an OVN upgrade is available.

        Make use of the installed package version and the package version
        available in the apt cache to determine availability of new version.
        """
        self.configure_sources()
        cur_vers = self.get_package_version(self.release_pkg,
                                            apt_cache_sufficient=False)
        avail_vers = self.get_package_version(self.release_pkg,
                                              apt_cache_sufficient=True)
        ch_fetch.apt_pkg.init()
        return ch_fetch.apt_pkg.version_compare(avail_vers, cur_vers) == 1

    def upgrade_if_available(self, interfaces_list):
        """Upgrade OVN if an upgrade is available.

        At present there is no need to pivot to a release specific charm class
        when upgrading OVN.  As such we override the default method to keep
        this simpler, given OVN versions are not fully represented in the
        OpenStack version machinery that the default method relies on.

        :param interfaces_list: List of instances of interface classes
        :returns: None
        """
        if self.ovn_upgrade_available(self.release_pkg):
            ch_core.hookenv.status_set('maintenance', 'Rolling upgrade')
            self.do_openstack_pkg_upgrade(upgrade_openstack=False)
            self.render_with_interfaces(interfaces_list)

    def configure_deferred_restarts(self):
        if 'enable-auto-restarts' in ch_core.hookenv.config().keys():
            deferred_events.configure_deferred_restarts(
                self.deferable_services)
            # Reactive charms execute perm missing.
            os.chmod(
                '/var/lib/charm/{}/policy-rc.d'.format(
                    ch_core.hookenv.service_name()),
                0o755)

    def states_to_check(self, required_relations=None):
        """Override parent method to add custom messaging.

        Note that this method will only override the messaging for certain
        relations, any relations we don't know about will get the default
        treatment from the parent method.

        :param required_relations: Override `required_relations` class instance
                                   variable.
        :type required_relations: Optional[List[str]]
        :returns: Map of relation name to flags to check presence of
                  accompanied by status and message.
        :rtype: collections.OrderedDict[str, List[Tuple[str, str, str]]]
        """
        # Retrieve default state map
        states_to_check = super().states_to_check(
            required_relations=required_relations)

        # The parent method will always return a OrderedDict
        if PEER_RELATION in states_to_check:
            # for the peer relation we want default messaging for all states
            # but connected.
            states_to_check[PEER_RELATION] = [
                ('{}.connected'.format(PEER_RELATION),
                 'blocked',
                 'Charm requires peers to operate, add more units. A minimum '
                 'of 3 is required for HA')
            ] + [
                states for states in states_to_check[PEER_RELATION]
                if 'connected' not in states[0]
            ]

        if CERT_RELATION in states_to_check:
            # for the certificates relation we want to replace all messaging
            states_to_check[CERT_RELATION] = [
                # the certificates relation has no connected state
                ('{}.available'.format(CERT_RELATION),
                 'blocked',
                 "'{}' missing".format(CERT_RELATION)),
                # we cannot proceed until Vault have provided server
                # certificates
                ('{}.server.certs.available'.format(CERT_RELATION),
                 'waiting',
                 "'{}' awaiting server certificate data"
                 .format(CERT_RELATION)),
            ]

        return states_to_check

    @staticmethod
    def ovn_sysconfdir():
        return '/etc/ovn'

    @staticmethod
    def ovn_rundir():
        return '/var/run/ovn'

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

    def validate_config(self):
        """Validate configuration and inform user of any issues.

        :returns: Tuple with status and message describing configuration issue.
        :rtype: Tuple[Optional[str],Optional[str]]
        """
        tgt_timer = self.config['ovsdb-server-election-timer']
        if (tgt_timer > self.max_election_timer or
                tgt_timer < self.min_election_timer):
            return (
                'blocked',
                "Invalid configuration: 'ovsdb-server-election-timer' must be "
                "> {} < {}."
                .format(self.min_election_timer, self.max_election_timer))
        return None, None

    def check_services_running(self):
        """
        The default charms.openstack/layer_openstack handler will use netcat to
        check if services are running but that causes the ovsdb-server logs to
        get spammed with SSL errors and warnings because netcat does not close
        the connection properly so instead we use ssl connections to test here.
        """
        _services, _ports = ch_cluster.get_managed_services_and_ports(
            self.services,
            self.ports_to_check(self.active_api_ports))
        key = os.path.join(self.ovn_sysconfdir(), 'key_host')
        cert = os.path.join(self.ovn_sysconfdir(), 'cert_host')
        ca_cert = os.path.join(self.ovn_sysconfdir(), 'ovn-central.crt')
        return os_utils.ows_check_services_running(use_ssl_check=True,
                                                   ssl_check_info={
                                                       'key': key,
                                                       'cert': cert,
                                                       'ca_cert': ca_cert},
                                                   services=_services,
                                                   ports=_ports)

    def custom_assess_status_last_check(self):
        """Customize charm status output.

        Checks and notifies for invalid config and adds clustered DB status to
        status message.

        :returns: Tuple with workload status and message.
        :rtype: Tuple[Optional[str],Optional[str]]
        """
        invalid_config = self.validate_config()
        if invalid_config != (None, None):
            return invalid_config

        cluster_str = self.cluster_status_message()
        if cluster_str:
            return ('active', 'Unit is ready ({})'.format(cluster_str))
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

    def cluster_status(self, db):
        """OVN version agnostic cluster_status helper.

        :param db: Database to operate on
        :type db: str
        :returns: Object describing the cluster status or None
        :rtype: Optional[ch_ovn.OVNClusterStatus]
        """
        try:
            # The charm will attempt to retrieve cluster status before OVN
            # is clustered and while units are paused, so we need to handle
            # errors from this call gracefully.
            return ch_ovn.cluster_status(db, rundir=self.ovn_rundir(),
                                         use_ovs_appctl=(
                                             self.release == 'train'))
        except (ValueError, subprocess.CalledProcessError) as e:
            ch_core.hookenv.log('Unable to get cluster status, ovsdb-server '
                                'not ready yet?: {}'.format(e),
                                level=ch_core.hookenv.DEBUG)
            return

    def cluster_status_message(self):
        """Get cluster status message suitable for use as workload message.

        :returns: Textual representation of local unit db and northd status.
        :rtype: str
        """
        db_leader = []
        for db in ('ovnnb_db', 'ovnsb_db',):
            status = self.cluster_status(db)
            if status and status.is_cluster_leader:
                db_leader.append(db)

        msg = []
        if db_leader:
            msg.append('leader: {}'.format(', '.join(db_leader)))
        if self.is_northd_active():
            msg.append('northd: active')
        return ' '.join(msg)

    def is_northd_active(self):
        """OVN version agnostic is_northd_active helper.

        :returns: True if northd is active, False if not, None if not supported
        :rtype: Optional[bool]
        """
        if self.release != 'train':
            return ch_ovn.is_northd_active()

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

        This function will return immediately if the database file already
        exists.

        Because of a shortcoming in the ``ovn-ctl`` script used to start the
        OVN databases we call to ``ovsdb-tool join-cluster`` ourself.

        That will create a database file on disk with the required information
        and the ``ovn-ctl`` script will not touch it.

        The ``ovn-ctl`` ``db-nb-cluster-remote-addr`` and
        ``db-sb-cluster-remote-addr`` configuration options only take one
        remote and one must be provided for correct startup, but the values in
        the on-disk database file will be used by the ``ovsdb-server`` process.

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
        if self.release == 'train':
            absolute_path = os.path.join('/var/lib/openvswitch', db_file)
        else:
            absolute_path = os.path.join('/var/lib/ovn', db_file)
        if os.path.exists(absolute_path):
            ch_core.hookenv.log('OVN database "{}" exists on disk, not '
                                'creating a new one joining cluster',
                                level=ch_core.hookenv.DEBUG)
            return
        cmd = ['ovsdb-tool', 'join-cluster', absolute_path, schema_name]
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
            with open(
                    self.options.ovn_ca_cert, 'w') as crt:
                chain = tls_object.get('chain')
                if chain:
                    crt.write(tls_object['ca'] + os.linesep + chain)
                else:
                    crt.write(tls_object['ca'])

            self.configure_cert(self.ovn_sysconfdir(),
                                tls_object['cert'],
                                tls_object['key'],
                                cn='host')
            break

    def configure_ovn_listener(self, db, port_map):
        """Create or update OVN listener configuration.

        :param db: Database to operate on, 'nb' or 'sb'
        :type db: str
        :param port_map: Dictionary with port number and associated settings
        :type port_map: Dict[int,Dict[str,str]]
        :raises: ValueError
        """
        if db not in ('nb', 'sb'):
            raise ValueError
        # NOTE: There is one individual OVSDB cluster leader for each
        # of the OVSDB databases and throughout a deployment lifetime
        # they are not necessarilly the same as the charm leader.
        #
        # However, at bootstrap time the OVSDB cluster leaders will
        # coincide with the charm leader.
        status = self.cluster_status('ovn{}_db'.format(db))
        if status and status.is_cluster_leader:
            ch_core.hookenv.log('is_cluster_leader {}'.format(db),
                                level=ch_core.hookenv.DEBUG)
            connections = ch_ovsdb.SimpleOVSDB(
                'ovn-{}ctl'.format(db)).connection
            for port, settings in port_map.items():
                ch_core.hookenv.log('port {} {}'.format(port, settings),
                                    level=ch_core.hookenv.DEBUG)
                # discover and create any non-existing listeners first
                for connection in connections.find(
                        'target="pssl:{}"'.format(port)):
                    break
                else:
                    ch_core.hookenv.log('create port {}'.format(port),
                                        level=ch_core.hookenv.DEBUG)
                    # NOTE(fnordahl) the listener configuration is written to
                    # the database and used by all units, so we cannot bind to
                    # specific space/address here.  We might consider not
                    # using listener configuration from DB, but that is
                    # currently not supported by ``ovn-ctl`` script.
                    self.run('ovn-{}ctl'.format(db),
                             '--',
                             '--id=@connection',
                             'create', 'connection',
                             'target="pssl:{}"'.format(port),
                             '--',
                             'add', '{}_Global'.format(db.upper()),
                             '.', 'connections', '@connection')
                # set/update connection settings
                for connection in connections.find(
                        'target="pssl:{}"'.format(port)):
                    for k, v in settings.items():
                        ch_core.hookenv.log(
                            'set {} {} {}'
                            .format(str(connection['_uuid']), k, v),
                            level=ch_core.hookenv.DEBUG)
                        connections.set(str(connection['_uuid']), k, v)

    def configure_ovsdb_election_timer(self, db, tgt_timer):
        """Set the OVSDB cluster Raft election timer.

        Note that the OVSDB Server will refuse to decrease or increase this
        value more than 2x the current value, however we should let the end
        user of the charm set this to whatever they want. Paper over the
        reality by iteratively decreasing / increasing the value in a safe
        pace.

        :param db: Database to operate on, 'nb' or 'sb'
        :type db: str
        :param tgt_timer: Target value for election timer in seconds
        :type tgt_timer: int
        :raises: ValueError
        """
        if db not in ('nb', 'sb'):
            raise ValueError
        if (tgt_timer > self.max_election_timer or
                tgt_timer < self.min_election_timer):
            # Invalid target timer, log error as well as inform user through
            # workload status+message, please refer to
            # `custom_assess_status_last_check` for implementation detail.
            ch_core.hookenv.log('Attempt to set election timer to invalid '
                                'value: {} (min {}, max {})'
                                .format(
                                    tgt_timer,
                                    self.min_election_timer,
                                    self.max_election_timer),
                                level=ch_core.hookenv.ERROR)
            return
        # OVN uses ms as unit for the election timer
        tgt_timer = tgt_timer * 1000

        ovn_db = 'ovn{}_db'.format(db)
        ovn_schema = 'OVN_Northbound' if db == 'nb' else 'OVN_Southbound'
        status = self.cluster_status(ovn_db)
        if status and status.is_cluster_leader:
            ch_core.hookenv.log('is_cluster_leader {}'.format(db),
                                level=ch_core.hookenv.DEBUG)
            cur_timer = status.election_timer
            if tgt_timer == cur_timer:
                ch_core.hookenv.log('Election timer already set to target '
                                    'value: {} == {}'
                                    .format(tgt_timer, cur_timer),
                                    level=ch_core.hookenv.DEBUG)
                return
            # to be able to reuse the change loop to both increase and decrease
            # the timer we assign the operators used to variables
            if tgt_timer > cur_timer:
                # when increasing timer, we will multiply the value
                change_op = operator.mul
                # when increasing timer, we want the smaller between target
                # value and current value multiplied with 2
                change_select = min
            else:
                # when decreasing timer, we will divide the value and do not
                # want fractional values
                change_op = operator.floordiv
                # when decreasing timer, we want the larger of target value and
                # current value divided by 2
                change_select = max
            while status and status.is_cluster_leader and (
                    status.election_timer != tgt_timer):
                # election timer decrease/increase cannot be more than 2x
                # current value per iteration
                change_timer = change_select(
                    change_op(cur_timer, 2), tgt_timer)
                ch_core.hookenv.status_set(
                    'maintenance',
                    'change {} election timer {}ms -> {}ms'
                    .format(ovn_schema, cur_timer, change_timer))
                ch_ovn.ovn_appctl(
                    ovn_db, (
                        'cluster/change-election-timer',
                        ovn_schema,
                        str(change_timer),
                    ),
                    rundir=self.ovn_rundir(),
                    use_ovs_appctl=(self.release == 'train'))
                # wait for an election window to pass before changing the value
                # again
                time.sleep((cur_timer + change_timer) / 1000)
                cur_timer = change_timer
                status = self.cluster_status(ovn_db)

    def configure_ovn(self, nb_port, sb_port, sb_admin_port):
        """Create or update OVN listener configuration.

        :param nb_port: Port for Northbound DB listener
        :type nb_port: int
        :param sb_port: Port for Southbound DB listener
        :type sb_port: int
        :param sb_admin_port: Port for cluster private Southbound DB listener
        :type sb_admin_port: int
        """
        inactivity_probe = int(
            self.config['ovsdb-server-inactivity-probe']) * 1000

        self.configure_ovn_listener(
            'nb', {
                nb_port: {
                    'inactivity_probe': inactivity_probe,
                },
            })
        self.configure_ovn_listener(
            'sb', {
                sb_port: {
                    'role': 'ovn-controller',
                    'inactivity_probe': inactivity_probe,
                },
            })
        self.configure_ovn_listener(
            'sb', {
                sb_admin_port: {
                    'inactivity_probe': inactivity_probe,
                },
            })

        election_timer = self.config['ovsdb-server-election-timer']
        self.configure_ovsdb_election_timer('nb', election_timer)
        self.configure_ovsdb_election_timer('sb', election_timer)

    @staticmethod
    def initialize_firewall():
        """Initialize firewall.

        Note that this function is disruptive to active connections and should
        only be called when necessary.
        """
        # set default allow
        ch_ufw.enable()
        ch_ufw.default_policy('allow', 'incoming')
        ch_ufw.default_policy('allow', 'outgoing')
        ch_ufw.default_policy('allow', 'routed')

    def configure_firewall(self, port_addr_map):
        """Configure firewall.

        Lock down access to ports not protected by OVN RBAC.

        :param port_addr_map: Map of ports to addresses to allow.
        :type port_addr_map: Dict[Tuple[int, ...], Optional[Iterator]]
        :param allowed_hosts: Hosts allowed to connect.
        :type allowed_hosts: Iterator
        """
        ufw_comment = 'charm-' + self.name

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

    def render_nrpe(self):
        """Configure Nagios NRPE checks."""
        hostname = nrpe.get_nagios_hostname()
        current_unit = nrpe.get_nagios_unit_name()
        charm_nrpe = nrpe.NRPE(hostname=hostname)
        self.add_nrpe_certs_check(charm_nrpe)
        nrpe.add_init_service_checks(
            charm_nrpe, self.nrpe_check_services, current_unit)
        charm_nrpe.write()

    def add_nrpe_certs_check(self, charm_nrpe):
        script = 'nrpe_check_ovn_certs.py'
        src = os.path.join(os.getenv('CHARM_DIR'), 'files', 'nagios', script)
        dst = os.path.join(NAGIOS_PLUGINS, script)
        rsync(src, dst)
        charm_nrpe.add_check(
            shortname='check_ovn_certs',
            description='Check that ovn certs are valid.',
            check_cmd=script
        )
        # Need to install this as a system package since it is needed by the
        # cron script that runs outside of the charm.
        ch_fetch.apt_install(['python3-cryptography'])
        script = 'check_ovn_certs.py'
        src = os.path.join(os.getenv('CHARM_DIR'), 'files', 'scripts', script)
        dst = os.path.join(SCRIPTS_DIR, script)
        rsync(src, dst)
        cronjob = CRONJOB_CMD.format(
            schedule='*/5 * * * *',
            command=dst)
        write_file(CERTCHECK_CRONFILE, cronjob)

    def custom_assess_status_check(self):
        """Report deferred events in charm status message."""
        state = None
        message = None
        deferred_events.check_restart_timestamps()
        events = collections.defaultdict(set)
        for e in deferred_events.get_deferred_events():
            events[e.action].add(e.service)
        for action, svcs in events.items():
            svc_msg = "Services queued for {}: {}".format(
                action, ', '.join(sorted(svcs)))
            state = 'active'
            if message:
                message = "{}. {}".format(message, svc_msg)
            else:
                message = svc_msg
        deferred_hooks = deferred_events.get_deferred_hooks()
        if deferred_hooks:
            state = 'active'
            svc_msg = "Hooks skipped due to disabled auto restarts: {}".format(
                ', '.join(sorted(deferred_hooks)))
            if message:
                message = "{}. {}".format(message, svc_msg)
            else:
                message = svc_msg
        return state, message

    def assess_exporter(self):
        is_installed = snap.is_installed('prometheus-ovn-exporter')
        channel = None
        channel = self.options.ovn_exporter_snap_channel
        if channel is None:
            if is_installed:
                snap.remove('prometheus-ovn-exporter')
            return

        if is_installed:
            snap.refresh('prometheus-ovn-exporter', channel=channel)
        else:
            snap.install('prometheus-ovn-exporter', channel=channel)
        snap.connect_all()

    @staticmethod
    def leave_cluster():
        """Run commands to remove servers running on this unit from cluster.

        In case the commands fail, an ERROR message will be logged.
        :return: None
        :rtype: None
        """
        try:
            ch_core.hookenv.log(
                "Removing self from Southbound cluster",
                ch_core.hookenv.INFO
            )
            ch_ovn.ovn_appctl("ovnsb_db", ("cluster/leave", "OVN_Southbound"))
        except subprocess.CalledProcessError:
            ch_core.hookenv.log(
                "Failed to leave Southbound cluster. You can use "
                "'cluster-kick' juju action on remaining units to "
                "remove lingering cluster members.",
                ch_core.hookenv.ERROR
            )

        try:
            ch_core.hookenv.log(
                "Removing self from Northbound cluster",
                ch_core.hookenv.INFO
            )
            ch_ovn.ovn_appctl("ovnnb_db", ("cluster/leave", "OVN_Northbound"))
        except subprocess.CalledProcessError:
            ch_core.hookenv.log(
                "Failed to leave Northbound cluster. You can use "
                "'cluster-kick' juju action on remaining units to "
                "remove lingering cluster members.",
                ch_core.hookenv.ERROR
            )

    @staticmethod
    def is_server_in_cluster(server_ip, cluster_status):
        """Parse cluster status and find if server with given IP is part of it.

        :param server_ip: IP of a server to search.
        :type server_ip: str
        :param cluster_status: Cluster status to parse.
        :type cluster_status: ch_ovn.OVNClusterStatus
        :return: True if server is part of the cluster. Otherwise, False.
        :rtype: bool
        """
        remote_unit_url = "ssl:{}:".format(server_ip)
        return any(
            list(server)[1].startswith(remote_unit_url)
            for server in cluster_status.servers
        )

    def wait_for_server_leave(self, server_ip, timeout=30):
        """Wait for servers with specified IP to leave SB and NB clusters.

        :param server_ip: IP of the server that should no longer be part of
            the clusters.
        :type server_ip: str
        :param timeout: How many seconds should this function wait for the
            servers to leave. The timeout should be an increment of 5.
        :return: True if servers from selected unit departed within the
            timeout window. Otherwise, it returns False.
        :rtype: bool
        """
        tick = 5
        timer = 0
        unit_in_sb_cluster = unit_in_nb_cluster = True
        servers_left = False
        wait_sb_msg = "Waiting for {} to leave Southbound cluster".format(
            server_ip
        )
        wait_nb_msg = "Waiting for {} to leave Northbound cluster".format(
            server_ip
        )
        while timer < timeout:
            if unit_in_sb_cluster:
                ch_core.hookenv.log(wait_sb_msg, ch_core.hookenv.INFO)
                unit_in_sb_cluster = self.is_server_in_cluster(
                    server_ip,
                    self.cluster_status("ovnsb_db")
                )
            if unit_in_nb_cluster:
                ch_core.hookenv.log(wait_nb_msg, ch_core.hookenv.INFO)
                unit_in_nb_cluster = self.is_server_in_cluster(
                    server_ip,
                    self.cluster_status("ovnnb_db")
                )
            if not unit_in_sb_cluster and not unit_in_nb_cluster:
                servers_left = True
                ch_core.hookenv.log(
                    "{} servers left Northbound and Southbound OVN "
                    "clusters.".format(server_ip),
                    ch_core.hookenv.INFO
                )
                break
            time.sleep(tick)
            timer += tick

        return servers_left


class TrainOVNCentralCharm(BaseOVNCentralCharm):
    # OpenvSwitch and OVN is distributed as part of the Ubuntu Cloud Archive
    # Pockets get their name from OpenStack releases
    release = 'train'

    # NOTE(fnordahl) we have to replace the package sysv init script with
    # systemd service files, this should be removed from the charm when the
    # systemd service files committed to Focal can be backported to the Train
    # UCA.
    #
    # The issue that triggered this change is that to be able to pass the
    # correct command line arguments to ``ovn-nortrhd`` we need to create
    # a ``/etc/openvswitch/ovn-northd-db-params.conf`` which has the side
    # effect of profoundly changing the behaviour of the ``ovn-ctl`` tool
    # that the ``ovn-central`` init script makes use of.
    #
    # https://github.com/ovn-org/ovn/blob/dc0e10c068c20c4e59c9c86ecee26baf8ed50e90/utilities/ovn-ctl#L323
    def __init__(self, **kwargs):
        """Override class init to adjust restart_map for Train.

        NOTE(fnordahl): the restart_map functionality in charms.openstack
        combines the process of writing a charm template to disk and
        restarting a service whenever the target file changes.

        In this instance we are only interested in getting the files written
        to disk.  The restart operation will be taken care of when
        ``/etc/default/ovn-central`` as defined in ``BaseOVNCentralCharm``.
        """
        super().__init__(**kwargs)
        self.restart_map.update({
            '/lib/systemd/system/ovn-central.service': [],
            '/lib/systemd/system/ovn-northd.service': [],
            '/lib/systemd/system/ovn-nb-ovsdb.service': [],
            '/lib/systemd/system/ovn-sb-ovsdb.service': [],
        })
        self.nrpe_check_services = [
            'ovn-northd',
            'ovn-nb-ovsdb',
            'ovn-sb-ovsdb',
        ]

    def install(self):
        """Override charm install method.

        NOTE(fnordahl) At Train, the OVN central components is packaged with
        a dependency on openvswitch-switch, but it does not need the switch
        or stock ovsdb running.
        """
        service_masks = [
            'openvswitch-switch.service',
            'ovs-vswitchd.service',
            'ovsdb-server.service',
            'ovn-central.service',
        ]
        super().install(service_masks=service_masks)

    @staticmethod
    def ovn_sysconfdir():
        return '/etc/openvswitch'

    @staticmethod
    def ovn_rundir():
        return '/var/run/openvswitch'


class UssuriOVNCentralCharm(BaseOVNCentralCharm):
    # OpenvSwitch and OVN is distributed as part of the Ubuntu Cloud Archive
    # Pockets get their name from OpenStack releases
    release = 'ussuri'

    def __init__(self, **kwargs):
        """Override class init to adjust service map for Ussuri."""
        super().__init__(**kwargs)
        # We need to list the OVN ovsdb-server services explicitly so they get
        # unmasked on render of ``ovn-central``.
        self.services.extend([
            'ovn-ovsdb-server-nb',
            'ovn-ovsdb-server-sb',
        ])
        self.nrpe_check_services = [
            'ovn-northd',
            'ovn-ovsdb-server-nb',
            'ovn-ovsdb-server-sb',
        ]

    def install(self, service_masks=None):
        """Override charm install method."""
        service_masks = service_masks or []
        if not reactive.is_flag_set('charm.installed'):
            # This is done to prevent extraneous standalone DB initialization
            # and subsequent upgrade to clustered DB when configuration is
            # rendered during the initial installation.
            # Masking of OVN services is skipped on subsequent calls to this
            # handler.
            service_masks.extend([
                'ovn-central.service',
                'ovn-ovsdb-server-nb.service',
                'ovn-ovsdb-server-sb.service',
            ])
        super().install(service_masks=service_masks)


class WallabyOVNCentralCharm(UssuriOVNCentralCharm):
    # OpenvSwitch and OVN is distributed as part of the Ubuntu Cloud Archive
    # Pockets get their name from OpenStack releases
    release = 'wallaby'
    packages = ['ovn-central', 'openstack-release']
