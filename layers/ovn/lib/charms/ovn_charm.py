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
import ipaddress
import os
import subprocess

import charms.reactive as reactive

import charmhelpers.core as ch_core
import charmhelpers.contrib.charmsupport.nrpe as nrpe
import charmhelpers.contrib.openstack.context as os_context
import charmhelpers.contrib.network.ovs as ch_ovs
import charmhelpers.contrib.network.ovs.ovsdb as ch_ovsdb

import charms_openstack.adapters
import charms_openstack.charm


CERT_RELATION = 'certificates'


class OVNConfigurationAdapter(
        charms_openstack.adapters.ConfigurationAdapter):
    """Provide a configuration adapter for OVN."""

    class OSContextObjectView(object):

        def __init__(self, ctxt):
            """Initialize OSContextObjectView instance.

            :param ctxt: Dictionary with context variables
            :type ctxt: Dict[str,any]
            """
            self.__dict__ = ctxt

    def __init__(self, **kwargs):
        """Initialize contexts consumed from charm helpers."""
        super().__init__(**kwargs)
        self._dpdk_device = self.OSContextObjectView(
            os_context.DPDKDeviceContext(
                bridges_key=self.charm_instance.bridges_key)())
        self._sriov_device = os_context.SRIOVContext()

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
    def dpdk_device(self):
        return self._dpdk_device

    @property
    def chassis_name(self):
        return self.charm_instance.get_ovs_hostname()

    @property
    def sriov_device(self):
        return self._sriov_device()


class NeutronPluginRelationAdapter(
        charms_openstack.adapters.OpenStackRelationAdapter):
    """Relation adapter for neutron-plugin interface."""

    @property
    def metadata_shared_secret(self):
        return self.relation.get_or_create_shared_secret()


class OVNChassisCharmRelationAdapters(
        charms_openstack.adapters.OpenStackRelationAdapters):
    """Provide dictionary of relation adapters for use by OVN Chassis charms.
    """
    relation_adapters = {
        # Note that RabbitMQ is only used for the Neutron SRIOV agent
        'amqp': charms_openstack.adapters.RabbitMQRelationAdapter,
        'nova_compute': NeutronPluginRelationAdapter,
    }


class BaseOVNChassisCharm(charms_openstack.charm.OpenStackCharm):
    """Base class for the OVN Chassis charms."""
    abstract_class = True
    package_codenames = {
        'ovn-host': collections.OrderedDict([
            ('2.12', 'train'),
            ('20.03', 'ussuri'),
            ('20.06', 'victoria'),
        ]),
    }
    release_pkg = 'ovn-host'
    adapters_class = OVNChassisCharmRelationAdapters
    configuration_class = OVNConfigurationAdapter
    required_relations = [CERT_RELATION, 'ovsdb']
    python_version = 3
    enable_openstack = False
    bridges_key = 'bridge-interface-mappings'
    nrpe_check_services = []

    def __init__(self, **kwargs):
        """Allow augmenting behaviour on external factors."""
        super().__init__(**kwargs)
        # NOTE: we must initialize the packages and services variables as
        # instance variables as we are extending them in the release
        # specialized class instances and can not rely on class variables.
        self.packages = ['ovn-host']
        self.services = ['ovn-host']
        # Note that we use the standard config render features of
        # charms.openstack to just copy this file in place hence no
        # service attached.
        #
        # The charm will configure the system-id at runtime in the
        # ``configure_ovs`` method.  The openvswitch-switch init script will
        # use the on-disk file on service restart.
        self.restart_map = {
            '/etc/openvswitch/system-id.conf': [],
        }

        if self.options.enable_dpdk:
            self.packages.extend(['openvswitch-switch-dpdk'])
            # The ``dpdk`` system init script takes care of binding devices
            # to the driver specified in configuration at run- and boot- time.
            #
            # NOTE: we must take care to perform device lookup and store
            # mapping information in the system before binding the interfaces
            # as important information such as hardware ethernet address (MAC)
            # will be harder to get at once bound. (The device disappears from
            # sysfs)
            self.restart_map.update({
                '/etc/dpdk/interfaces': ['dpdk'],
            })

        if self.options.enable_hardware_offload or self.options.enable_sriov:
            # The ``sriov-netplan-shim`` package does boot- and run-time
            # configuration of Virtual Functions (VFs) in the system.
            #
            # NOTE: We consume the ``sriov-netplan-shim`` package both as a
            # charm wheel for the PCI Python library parts and as a deb for
            # the system init script and configuration tools.
            self.packages.append('sriov-netplan-shim')
            vf_changed_svcs = ['sriov-netplan-shim']
            if self.options.enable_hardware_offload:
                self.packages.append('mlnx-switchdev-mode')
                vf_changed_svcs.append('mlnx-switchdev-mode')
            self.restart_map.update({
                '/etc/sriov-netplan-shim/interfaces.yaml': vf_changed_svcs,
            })

        if reactive.is_flag_set('charm.ovn-chassis.enable-openstack'):
            self.enable_openstack = True
            # When OpenStack support is enabled the various config files laid
            # out for Neutron agents need to have group ownership of 'neutron'
            # for the services to have access to them.
            self.group = 'neutron'
            if self.options.enable_sriov:
                self.packages.append('neutron-sriov-agent')
                self.restart_map.update({
                    '/etc/neutron/neutron.conf': ['neutron-sriov-agent'],
                    '/etc/neutron/plugins/ml2/sriov_agent.ini': [
                        'neutron-sriov-agent'],
                })
                if 'amqp' not in self.required_relations:
                    self.required_relations.append('amqp')
            elif self.options.enable_dpdk:
                # Note that we use the standard config render features of
                # charms.openstack to just copy this file in place hence no
                # service attached. systemd-tmpfiles-setup will take care of
                # it at boot and we will do a first-time initialization in the
                # ``install`` method.
                self.restart_map.update({
                    '/etc/tmpfiles.d/nova-ovs-vhost-user.conf': []})

    def install(self):
        """Extend the default install method to handle update-alternatives.
        """
        if self.options.enable_hardware_offload or self.options.enable_sriov:
            self.configure_source('networking-tools-source')

        super().install()

        if self.options.enable_dpdk:
            self.run('update-alternatives', '--set', 'ovs-vswitchd',
                     '/usr/lib/openvswitch-switch-dpdk/ovs-vswitchd-dpdk')
            # Do first-time initialization of the directory used for vhostuser
            # sockets.  Neutron is made aware of this path centrally by the
            # neutron-api-plugin-ovn charm.
            #
            # Neutron will make per chassis decisions based on chassis
            # configuration whether vif_type will be 'ovs' or 'vhostuser'.
            #
            # This allows having a mix of DPDK and non-DPDK nodes in the same
            # deployment.
            if self.enable_openstack and not os.path.exists(
                    '/run/libvirt-vhost-user'):
                self.run('systemd-tmpfiles', '--create')
        else:
            self.run('update-alternatives', '--set', 'ovs-vswitchd',
                     '/usr/lib/openvswitch-switch/ovs-vswitchd')

    def resume(self):
        """Do full hook execution on resume.

        A part of the migration strategy to OVN is to start the chassis charms
        paused only to resume them as soon as cleanup after the previous SDN
        has been completed.

        For this to work we need to run a full hook execution on resume to make
        sure the system is configured properly.
        """
        super().resume()

        ch_core.hookenv.log("Re-execing as full hook execution after resume.",
                            level=ch_core.hookenv.INFO)
        os.execl(
            '/usr/bin/env',
            'python3',
            os.path.join(ch_core.hookenv.charm_dir(), 'hooks/config-changed'),
        )

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
        """Provide path to OVN system configuration."""
        return '/etc/ovn'

    def run(self, *args):
        """Run external process and return result.

        :param *args: Command name and arguments.
        :type *args: str
        :returns: Data about completed process
        :rtype: subprocess.CompletedProcess
        :raises: subprocess.CalledProcessError
        """
        cp = subprocess.run(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True,
            universal_newlines=True)
        ch_core.hookenv.log(cp, level=ch_core.hookenv.INFO)

    def get_certificate_requests(self):
        """Override default certificate request handler.

        We make use of OVN RBAC for authorization of writes from chassis nodes
        to the Southbound DB. The OVSDB server implementation makes use of the
        CN in the certificate to grant access to individual chassis. The
        chassis name and CN must match for this to work.
        """
        return {self.get_ovs_hostname(): {'sans': []}}

    def configure_tls(self, certificates_interface=None):
        """Override default handler prepare certs per OVNs taste.

        :param certificates_interface: A certificates relation
        :type certificates_interface: Optional[TlsRequires(reactive.Endpoint)]
        """
        # The default handler in ``OpenStackCharm`` class does the CA only
        tls_objects = self.get_certs_and_keys(
            certificates_interface=certificates_interface)
        if not tls_objects:
            # We have no configuration settings ssl_* nor a certificates
            # relation to vault.
            # Avoid LP Bug#1900457
            ch_core.hookenv.log(
                'No TLS objects available yet. Deferring TLS processing',
                level=ch_core.hookenv.DEBUG)
            return

        expected_cn = self.get_ovs_hostname()

        for tls_object in tls_objects:
            if tls_object.get('cn') != expected_cn:
                continue
            with open(self.options.ovn_ca_cert, 'w') as crt:
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
        else:
            ch_core.hookenv.log('No certificate with CN matching hostname '
                                'configured in OVS: "{}"'
                                .format(expected_cn),
                                level=ch_core.hookenv.INFO)
            # Flag that we are not satisfied with the provided certificates
            reactive.clear_flag('certificates.available')

    @staticmethod
    def _format_addr(addr):
        """Validate and format IP address

        :param addr: IPv6 or IPv4 address
        :type addr: str
        :returns: Address string, optionally encapsulated in brackets ([])
        :rtype: str
        :raises: ValueError
        """
        ipaddr = ipaddress.ip_address(addr)
        if isinstance(ipaddr, ipaddress.IPv6Address):
            fmt = '[{}]'
        else:
            fmt = '{}'
        return fmt.format(ipaddr)

    def get_data_ip(self):
        """Get IP of interface bound to ``data`` binding.

        :returns: IP address
        :rtype: str
        """
        # juju will always return address information, regardless of actual
        # presence of space binding.
        #
        # Unpack ourself as ``network_get_primary_address`` is deprecated
        return self._format_addr(
            ch_core.hookenv.network_get(
                'data')['bind-addresses'][0]['addresses'][0]['address'])

    @staticmethod
    def get_ovs_hostname():
        """Get hostname (FQDN) from Open vSwitch.

        :returns: Hostname as configured in Open vSwitch
        :rtype: str
        :raises: KeyError
        """
        # The Open vSwitch ``ovs-ctl`` script has already done the dirty work
        # of retrieving the hosts FQDN, use it.
        #
        # In the unlikely event of the hostname not being set in the database
        # we want to error out as this will cause malfunction.
        for row in ch_ovsdb.SimpleOVSDB('ovs-vsctl').open_vswitch:
            return row['external_ids']['hostname']

    def configure_ovs_dpdk(self):
        """Configure DPDK specific bits in Open vSwitch.

        :returns: Whether something changed
        :rtype: bool
        """
        something_changed = False
        dpdk_context = os_context.OVSDPDKDeviceContext(
            bridges_key=self.bridges_key)
        opvs = ch_ovsdb.SimpleOVSDB('ovs-vsctl').open_vswitch
        other_config_fmt = 'other_config:{}'
        for row in opvs:
            for k, v in (('dpdk-lcore-mask', dpdk_context.cpu_mask()),
                         ('dpdk-socket-mem', dpdk_context.socket_memory()),
                         ('dpdk-init', 'true'),
                         ('dpdk-extra', dpdk_context.pci_whitelist()),
                         ):
                if row.get(other_config_fmt.format(k)) != v:
                    something_changed = True
                    if v:
                        opvs.set('.', other_config_fmt.format(k), v)
                    else:
                        opvs.remove('.', 'other_config', k)
        return something_changed

    def configure_ovs_hw_offload(self):
        """Configure hardware offload specific bits in Open vSwitch.

        :returns: Whether something changed
        :rtype: bool
        """
        something_changed = False
        opvs = ch_ovsdb.SimpleOVSDB('ovs-vsctl').open_vswitch
        other_config_fmt = 'other_config:{}'
        for row in opvs:
            for k, v in (('hw-offload', 'true'),
                         ('max-idle', '30000'),
                         ):
                if row.get(other_config_fmt.format(k)) != v:
                    something_changed = True
                    if v:
                        opvs.set('.', other_config_fmt.format(k), v)
                    else:
                        opvs.remove('.', 'other_config', k)
        return something_changed

    def configure_ovs(self, sb_conn):
        """Global Open vSwitch configuration tasks.

        :param sb_conn: Comma separated string of OVSDB connection methods.
        :type sb_conn: str

        Note that running this method will restart the ``openvswitch-switch``
        service if required.
        """
        if self.check_if_paused() != (None, None):
            ch_core.hookenv.log('Unit is paused, defer global Open vSwitch '
                                'configuration tasks.',
                                level=ch_core.hookenv.INFO)
            return
        # Must make sure the service runs otherwise calls to ``ovs-vsctl`` will
        # hang.
        ch_core.host.service_start('openvswitch-switch')

        restart_required = False
        # NOTE(fnordahl): Due to what is probably a bug in Open vSwitch
        # subsequent calls to ``ovs-vsctl set-ssl`` will hang indefinitely
        # Work around this by passing ``--no-wait``.
        self.run('ovs-vsctl',
                 '--no-wait',
                 'set-ssl',
                 self.options.ovn_key,
                 self.options.ovn_cert,
                 self.options.ovn_ca_cert)

        # The local ``ovn-controller`` process will retrieve information about
        # how to connect to OVN from the local Open vSwitch database.
        self.run('ovs-vsctl',
                 'set', 'open', '.',
                 'external-ids:ovn-encap-type=geneve', '--',
                 'set', 'open', '.',
                 'external-ids:ovn-encap-ip={}'
                 .format(self.get_data_ip()), '--',
                 'set', 'open', '.',
                 'external-ids:system-id={}'
                 .format(self.get_ovs_hostname()))
        self.run('ovs-vsctl',
                 'set',
                 'open',
                 '.',
                 'external-ids:ovn-remote={}'
                 .format(sb_conn))
        if self.enable_openstack:
            # OpenStack Nova expects the local OVSDB server to listen to
            # TCP port 6640 on localhost.  We use this for the OVN metadata
            # agent too, as it allows us to run it as a non-root user.
            # LP: #1852200
            target = 'ptcp:6640:127.0.0.1'
            for el in ch_ovsdb.SimpleOVSDB(
                    'ovs-vsctl').manager.find('target="{}"'.format(target)):
                break
            else:
                self.run('ovs-vsctl', '--id', '@manager',
                         'create', 'Manager', 'target="{}"'.format(target),
                         '--', 'add', 'Open_vSwitch', '.', 'manager_options',
                         '@manager')
        if self.options.enable_hardware_offload:
            restart_required = self.configure_ovs_hw_offload()
        elif self.options.enable_dpdk:
            restart_required = self.configure_ovs_dpdk()
        if restart_required:
            ch_core.host.service_restart('openvswitch-switch')

    def configure_bridges(self):
        """Configure Open vSwitch bridges ports and interfaces."""
        if self.check_if_paused() != (None, None):
            ch_core.hookenv.log('Unit is paused, defer Open vSwitch bridge '
                                'port interface configuration tasks.',
                                level=ch_core.hookenv.INFO)
            return
        bpi = os_context.BridgePortInterfaceMap(bridges_key=self.bridges_key)
        bond_config = os_context.BondConfig()
        ch_core.hookenv.log('BridgePortInterfaceMap: "{}"'.format(bpi.items()),
                            level=ch_core.hookenv.DEBUG)

        # build map of bridges to ovn networks with existing if-mapping on host
        # and at the same time build ovn-bridge-mappings string
        ovn_br_map_str = ''
        ovnbridges = collections.defaultdict(list)
        config_obm = self.config['ovn-bridge-mappings'] or ''
        for pair in sorted(config_obm.split()):
            network, bridge = pair.split(':', 1)
            if bridge in bpi:
                ovnbridges[bridge].append(network)
                if ovn_br_map_str:
                    ovn_br_map_str += ','
                ovn_br_map_str += '{}:{}'.format(network, bridge)

        bridges = ch_ovsdb.SimpleOVSDB('ovs-vsctl').bridge
        ports = ch_ovsdb.SimpleOVSDB('ovs-vsctl').port
        for bridge in bridges.find('external_ids:charm-ovn-chassis=managed'):
            # remove bridges and ports that are managed by us and no longer in
            # config
            if bridge['name'] not in bpi and bridge['name'] != 'br-int':
                ch_core.hookenv.log('removing bridge "{}" as it is no longer'
                                    'present in configuration for this unit.'
                                    .format(bridge['name']),
                                    level=ch_core.hookenv.DEBUG)
                ch_ovs.del_bridge(bridge['name'])
            else:
                for port in ports.find('external_ids:charm-ovn-chassis={}'
                                       .format(bridge['name'])):
                    if port['name'] not in bpi[bridge['name']]:
                        ch_core.hookenv.log('removing port "{}" from bridge '
                                            '"{}" as it is no longer present '
                                            'in configuration for this unit.'
                                            .format(port['name'],
                                                    bridge['name']),
                                            level=ch_core.hookenv.DEBUG)
                        ch_ovs.del_bridge_port(bridge['name'], port['name'])
        brdata = {
            'external-ids': {'charm-ovn-chassis': 'managed'},
            'protocols': 'OpenFlow13,OpenFlow15',
        }
        if self.options.enable_dpdk:
            brdata.update({'datapath-type': 'netdev'})
        else:
            brdata.update({'datapath-type': 'system'})
        # we always update the integration bridge to make sure it has settings
        # apropriate for the current charm configuration
        ch_ovs.add_bridge('br-int', brdata={
            **brdata,
            **{
                # for the integration bridge we want the datapath to await
                # controller action before adding any flows. This is to avoid
                # switching packets between isolated logical networks before
                # `ovn-controller` starts up.
                'fail-mode': 'secure',
                # Suppress in-band control flows for the integration bridge,
                # refer to ovn-architecture(7) for more details.
                'other-config': {'disable-in-band': 'true'},
            },
        })
        for br in bpi:
            if br not in ovnbridges:
                continue
            ch_ovs.add_bridge(br, brdata={
                **brdata,
                # for bridges used for external connectivity we want the
                # datapath to act like an ordinary MAC-learning switch.
                **{'fail-mode': 'standalone'},
            })
            for port in bpi[br]:
                ifdatamap = bpi.get_ifdatamap(br, port)
                ifdatamap = {
                    port: {
                        **ifdata,
                        **{'external-ids': {'charm-ovn-chassis': br}},
                    }
                    for port, ifdata in ifdatamap.items()
                }

                if len(ifdatamap) > 1:
                    ch_ovs.add_bridge_bond(br, port, list(ifdatamap.keys()),
                                           bond_config.get_ovs_portdata(port),
                                           ifdatamap)
                else:
                    ch_ovs.add_bridge_port(br, port,
                                           ifdata=ifdatamap.get(port, {}),
                                           linkup=not self.options.enable_dpdk,
                                           promisc=None,
                                           portdata={
                                               'external-ids': {
                                                   'charm-ovn-chassis': br}})

        opvs = ch_ovsdb.SimpleOVSDB('ovs-vsctl').open_vswitch
        if ovn_br_map_str:
            opvs.set('.', 'external_ids:ovn-bridge-mappings', ovn_br_map_str)
            # NOTE(fnordahl): Workaround for LP: #1848757
            opvs.set('.', 'external_ids:ovn-cms-options',
                     'enable-chassis-as-gw')
        else:
            opvs.remove('.', 'external_ids', 'ovn-bridge-mappings')
            # NOTE(fnordahl): Workaround for LP: #1848757
            opvs.remove('.', 'external_ids', 'ovn-cms-options')

    def render_nrpe(self):
        """Configure Nagios NRPE checks."""
        ch_core.hookenv.log("Rendering NRPE checks.",
                            level=ch_core.hookenv.INFO)
        hostname = nrpe.get_nagios_hostname()
        current_unit = nrpe.get_nagios_unit_name()
        # Determine if this is a subordinate unit or not
        if ch_core.hookenv.principal_unit() == ch_core.hookenv.local_unit():
            primary = True
        else:
            primary = False
        charm_nrpe = nrpe.NRPE(hostname=hostname, primary=primary)
        nrpe.add_init_service_checks(
            charm_nrpe, self.nrpe_check_services, current_unit)
        charm_nrpe.write()


class BaseTrainOVNChassisCharm(BaseOVNChassisCharm):
    """Train incarnation of the OVN Chassis base charm class."""
    abstract_class = True

    @staticmethod
    def ovn_sysconfdir():
        return '/etc/openvswitch'

    def __init__(self, **kwargs):
        """Allow augmenting behaviour on external factors."""
        super().__init__(**kwargs)
        self.nrpe_check_services = [
            'ovn-host',
            'ovs-vswitchd',
            'ovsdb-server',
        ]
        if self.enable_openstack:
            metadata_agent = 'networking-ovn-metadata-agent'
            self.nrpe_check_services.append(metadata_agent)
            self.packages.extend(['networking-ovn-metadata-agent', 'haproxy'])
            self.services.append(metadata_agent)
            self.restart_map.update({
                '/etc/neutron/'
                'networking_ovn_metadata_agent.ini': [metadata_agent],
            })


class BaseUssuriOVNChassisCharm(BaseOVNChassisCharm):
    """Ussuri incarnation of the OVN Chassis base charm class."""
    abstract_class = True

    def __init__(self, **kwargs):
        """Allow augmenting behaviour on external factors."""
        super().__init__(**kwargs)
        self.nrpe_check_services = [
            'ovn-controller',
            'ovs-vswitchd',
            'ovsdb-server',
        ]
        if self.enable_openstack:
            metadata_agent = 'neutron-ovn-metadata-agent'
            self.nrpe_check_services.append(metadata_agent)
            self.packages.extend([metadata_agent])
            self.services.append(metadata_agent)
            self.restart_map.update({
                '/etc/neutron/neutron_ovn_metadata_agent.ini': [
                    metadata_agent],
            })
