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
               'leadership.is_leader')
def announce_leader_ready():
    """Announce leader is ready.

    At this point ovn-ctl has taken care of initialization of OVSDB databases
    and OVSDB servers for the Northbound- and Southbound- databases are
    running.

    Signal to our peers that they should render configurations and start their
    database processes.
    """
    # FIXME use the OVSDB cluster and/or server IDs here?
    leadership.leader_set({'ready': True})


@reactive.when_not('leadership.set.ready')
@reactive.when('charm.installed', 'leadership.is_leader')
def initialize_ovsdbs():
    with charm.provide_charm_instance() as ovn_charm:
        # this will render the ``/etc/default/ovn-central`` file without
        # configuration for the cluster remote addresses which in turn
        # leads ``ovn-ctl`` on the path to initializing a new cluster
        ovn_charm.render_with_interfaces([])
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
            reactive.set_flag('config.rendered')
        ovn_charm.assess_status()
