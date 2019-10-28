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

import charms.reactive as reactive
import charms.leadership as leadership

import charms_openstack.bus
import charms_openstack.charm as charm

import charmhelpers.core as ch_core

import charm.ovs as ovs


charms_openstack.bus.discover()

# Use the charms.openstack defaults for common states and hooks
charm.use_defaults(
    'charm.installed',
    'config.changed',
    'update-status',
    'upgrade-charm',
)


# XXX this has not received testing, so disabling config options
# for now.
@reactive.when_not_all('config.default.ssl_ca',
                       'config.default.ssl_cert',
                       'config.default.ssl_key')
@reactive.when('config.rendered', 'config.changed')
def certificates_in_config_tls():
    # handle the legacy ssl_* configuration options
    with charm.provide_charm_instance() as ovn_charm:
        ovn_charm.configure_tls()
        ovn_charm.assess_status()


@reactive.when('config.rendered',
               'certificates.connected',
               'certificates.available',
               'leadership.is_leader',
               'ovsdb-peer.connected',)
def announce_leader_ready():
    """Announce leader is ready.

    At this point ovn-ctl has taken care of initialization of OVSDB databases
    and OVSDB servers for the Northbound- and Southbound- databases are
    running.

    Signal to our peers that they should render configurations and start their
    database processes.
    """
    # although this is done in the interface, explicitly do it in the same
    # breath as updating the leader settings as our peers will immediately
    # look for it
    ovsdb_peer = reactive.endpoint_from_flag('ovsdb-peer.connected')
    ovsdb_peer.publish_cluster_local_addr()

    # FIXME use the OVSDB cluster and/or server IDs here?
    leadership.leader_set({'ready': True})


@reactive.when_not('leadership.set.ready')
@reactive.when('charm.installed', 'leadership.is_leader',
               'ovsdb-peer.connected')
def initialize_ovsdbs():
    ovsdb_peer = reactive.endpoint_from_flag('ovsdb-peer.connected')
    with charm.provide_charm_instance() as ovn_charm:
        # ovsdb_peer at connected state will not provide remote addresses
        # for the cluster.  this will render the ``/etc/default/ovn-central``
        # file without configuration for the cluster remote addresses which
        # in turn leads ``ovn-ctl`` on the path to initializing a new cluster
        ovn_charm.render_with_interfaces([ovsdb_peer])
        if ovn_charm.enable_services():
            # belated enablement of default certificates handler due to the
            # ``ovsdb-server`` processes must have finished database
            # initialization and be running prior to configuring TLS
            charm.use_defaults('certificates.available')
            reactive.set_flag('config.rendered')
        ovn_charm.assess_status()


@reactive.when_not('leadership.is_leader')
@reactive.when('charm.installed')
def enable_default_certificates():
    # belated enablement of default certificates handler due to the
    # ``ovsdb-server`` processes must have finished database
    # initialization and be running prior to configuring TLS
    charm.use_defaults('certificates.available')


@reactive.when('ovsdb-peer.available',
               'leadership.set.ready',
               'certificates.connected',
               'certificates.available')
def publish_addr_to_clients():
    ovsdb_peer = reactive.endpoint_from_flag('ovsdb-peer.available')
    for ep in [reactive.endpoint_from_flag('ovsdb.connected'),
               reactive.endpoint_from_flag('ovsdb-cms.connected')]:
        if not ep:
            continue
        ep.publish_cluster_local_addr(ovsdb_peer.cluster_local_addr)


@reactive.when('ovsdb-peer.available',
               'leadership.set.ready',
               'certificates.connected',
               'certificates.available')
def render():
    ovsdb_peer = reactive.endpoint_from_flag('ovsdb-peer.available')
    with charm.provide_charm_instance() as ovn_charm:
        ovn_charm.render_with_interfaces([ovsdb_peer])
        # NOTE: The upstream ctl scripts currently do not support passing
        # multiple connection strings to the ``ovsdb-tool join-cluster``
        # command.
        #
        # This makes it harder to bootstrap a cluster in the event
        # one of the units are not available.  Thus the charm performs the
        # ``join-cluster`` command expliclty before handing off to the
        # upstream scripts.
        #
        # Replace this with functionality in ``ovn-ctl`` when support has been
        # added upstream.
        ovn_charm.join_cluster('/var/lib/openvswitch/ovnnb_db.db',
                               'OVN_Northbound',
                               ovsdb_peer.db_connection_strs(
                                   (ovsdb_peer.cluster_local_addr,),
                                   ovsdb_peer.db_nb_cluster_port),
                               ovsdb_peer.db_connection_strs(
                                   ovsdb_peer.cluster_remote_addrs,
                                   ovsdb_peer.db_nb_cluster_port))
        ovn_charm.join_cluster('/var/lib/openvswitch/ovnsb_db.db',
                               'OVN_Southbound',
                               ovsdb_peer.db_connection_strs(
                                   (ovsdb_peer.cluster_local_addr,),
                                   ovsdb_peer.db_sb_cluster_port),
                               ovsdb_peer.db_connection_strs(
                                   ovsdb_peer.cluster_remote_addrs,
                                   ovsdb_peer.db_sb_cluster_port))
        if ovn_charm.enable_services():
            ovn_charm.configure_ovn_remote(ovsdb_peer)
            reactive.set_flag('config.rendered')
        ovn_charm.assess_status()


# @reactive.when('leadership.set.ready', 'ovsdb.connected',
#                'endpoint.ovsdb.changed.chassis')
@reactive.when('leadership.set.ready', 'ovsdb.connected')
def register_chassis():
    ovsdb = reactive.endpoint_from_flag('ovsdb.connected')
    if ovs.is_cluster_leader('/var/run/openvswitch/ovnsb_db.ctl',
                             'OVN_Southbound'):
        sb_encap = ovs.SimpleOVSDB('ovn-sbctl', 'encap')
        for entry in ovsdb.get_chassis():
            for chassis, encap in entry.items():
                # TODO: We need to update interface data model to be a list of
                # encap types, and we need a higher level function to compare
                # the remote charm registration with the multiple encap rows
                # in the SB DB to be able to detect when to update.
                for enc in sb_encap.find('chassis_name={}'.format(chassis)):
                    if (enc['type'] == encap[0] and enc['ip'] == encap[1]):
                        ch_core.hookenv.log('skip registering already '
                                            'existing and up to date chassis.',
                                            level=ch_core.hookenv.DEBUG)
                        break
                else:
                    ovs.del_chassis(chassis)
                    ovs.add_chassis(chassis, encap[0], encap[1])

    ch_core.hookenv.log('DEBUG: register_chassis "{}"'
                        .format(list(ovsdb.get_chassis())),
                        level=ch_core.hookenv.INFO)
    reactive.clear_flag('endpoint.ovsdb.changed.chassis')
