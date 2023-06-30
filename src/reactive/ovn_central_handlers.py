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
import charms.coordinator as coordinator

import charms_openstack.bus
import charms_openstack.charm as charm

from charmhelpers.core import hookenv


charms_openstack.bus.discover()

# Use the charms.openstack defaults for common states and hooks
charm.use_defaults(
    'config.changed',
    'update-status',
    'upgrade-charm',
)


@reactive.when_none('charm.installed', 'leadership.set.install_stamp')
@reactive.when('leadership.is_leader')
def stamp_fresh_deployment():
    """Stamp the deployment with leader setting, fresh deployment.

    This is used to determine whether this application is a fresh or upgraded
    deployment which influence the default of the `ovn-source` configuration
    option.
    """
    leadership.leader_set(install_stamp=2203)


@reactive.when_none('is-update-status-hook',
                    'leadership.set.install_stamp',
                    'leadership.set.upgrade_stamp')
@reactive.when('charm.installed',
               'leadership.is_leader')
def stamp_upgraded_deployment():
    """Stamp the deployment with leader setting, upgrade.

    This is needed so that the units of this application can safely enable
    the default install hook.
    """
    leadership.leader_set(upgrade_stamp=2203)


@reactive.when_none('charm.installed', 'is-update-status-hook')
@reactive.when_any('leadership.set.install_stamp',
                   'leadership.set.upgrade_stamp')
def enable_install():
    """Enable the default install hook."""
    charm.use_defaults('charm.installed')

    # These flags will be set on initial install.  We use these flags to ensure
    # not performing certain actions during coordinated payload upgrades, but
    # we don't want these provisions to interfere with initial clustering.
    reactive.clear_flag('config.changed.source')
    reactive.clear_flag('config.changed.ovn-source')


@reactive.when_none('is-update-status-hook', 'charm.firewall_initialized')
def initialize_firewall():
    """Do one-time initialization of firewall."""
    with charm.provide_charm_instance() as ovn_charm:
        ovn_charm.initialize_firewall()
        reactive.set_flag('charm.firewall_initialized')


@reactive.when_none('is-update-status-hook',
                    'leadership.set.nb_cid',
                    'leadership.set.sb_cid',
                    'coordinator.granted.upgrade',
                    'coordinator.requested.upgrade',
                    'config.changed.source',
                    'config.changed.ovn-source')
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

    ovsdb = reactive.endpoint_from_name('ovsdb')
    with charm.provide_charm_instance() as ovn_charm:
        # Create and configure listeners
        ovn_charm.configure_ovn(
            ovsdb_peer.db_nb_port,
            ovsdb.db_sb_port,
            ovsdb_peer.db_sb_admin_port)
        nb_status = ovn_charm.cluster_status('ovnnb_db')
        sb_status = ovn_charm.cluster_status('ovnsb_db')
        leadership.leader_set({
            'ready': True,
            'nb_cid': str(nb_status.cluster_id),
            'sb_cid': str(sb_status.cluster_id),
        })


@reactive.when_none('is-update-status-hook',
                    'leadership.set.nb_cid',
                    'leadership.set.sb_cid',
                    'coordinator.granted.upgrade',
                    'coordinator.requested.upgrade')
@reactive.when('charm.installed', 'leadership.is_leader',
               'ovsdb-peer.connected')
def initialize_ovsdbs():
    ovsdb_peer = reactive.endpoint_from_flag('ovsdb-peer.connected')
    with charm.provide_charm_instance() as ovn_charm:
        # On the leader the ``/etc/default/ovn-central`` file is rendered
        # without configuration for the cluste remote address. This leads
        # ``ovn-ctl`` on the path to initializing a new cluster if the
        # database file does not already exist.
        ovn_charm.render_with_interfaces([ovsdb_peer])
        if ovn_charm.enable_services():
            # belated enablement of default certificates handler due to the
            # ``ovsdb-server`` processes must have finished database
            # initialization and be running prior to configuring TLS
            charm.use_defaults('certificates.available')
            reactive.set_flag('config.rendered')
        ovn_charm.assess_status()


@reactive.when_none('is-update-status-hook', 'leadership.is_leader')
@reactive.when('charm.installed')
def enable_default_certificates():
    # belated enablement of default certificates handler due to the
    # ``ovsdb-server`` processes must have finished database
    # initialization and be running prior to configuring TLS
    charm.use_defaults('certificates.available')


@reactive.when_none('is-update-status-hook', 'endpoint.ovsdb-peer.departed')
@reactive.when('ovsdb-peer.available')
def configure_firewall():
    ovsdb_peer = reactive.endpoint_from_flag('ovsdb-peer.available')
    ovsdb_cms = reactive.endpoint_from_flag('ovsdb-cms.connected')
    with charm.provide_charm_instance() as ovn_charm:
        ovn_charm.configure_firewall({
            (ovsdb_peer.db_nb_port,
                ovsdb_peer.db_sb_admin_port,
                ovsdb_peer.db_sb_cluster_port,
                ovsdb_peer.db_nb_cluster_port,):
            ovsdb_peer.cluster_remote_addrs,
            # NOTE(fnordahl): Tactical workaround for LP: #1864640
            (ovsdb_peer.db_nb_port,
                ovsdb_peer.db_sb_admin_port,):
            ovsdb_cms.client_remote_addrs if ovsdb_cms else None,
        })
        ovn_charm.assess_status()


@reactive.when_none('is-update-status-hook')
@reactive.when('ovsdb-peer.available',
               'leadership.set.nb_cid',
               'leadership.set.sb_cid',
               'certificates.connected',
               'certificates.available')
def publish_addr_to_clients():
    ovsdb_peer = reactive.endpoint_from_flag('ovsdb-peer.available')
    for ep in [reactive.endpoint_from_flag('ovsdb.connected'),
               reactive.endpoint_from_flag('ovsdb-cms.connected')]:
        if not ep:
            continue
        ep.publish_cluster_local_addr(ovsdb_peer.cluster_local_addr)


@reactive.when_none('is-update-status-hook')
@reactive.when('ovsdb-peer.available')
@reactive.when_any('config.changed.source', 'config.changed.ovn-source')
def maybe_request_upgrade():
    # The ovn-ctl script in the ovn-common package does schema upgrade based
    # on non-presence of a value to `--db-nb-cluster-remote-addr` in
    # /etc/default/ovn-central.  This is the case for the charm leader.
    #
    # The charm leader will perform DB schema upgrade as part of the package
    # upgrade, and in order to succeed with that we must ensure the other
    # units does not perform the package upgrade simultaneously.
    #
    # The coordinator library is based on leader storage and the leader will
    # always be the first one to get the lock.
    coordinator.acquire('upgrade')


@reactive.when_none('is-update-status-hook')
@reactive.when('ovsdb-peer.available', 'coordinator.granted.upgrade')
def maybe_do_upgrade():
    ovsdb_peer = reactive.endpoint_from_flag('ovsdb-peer.available')
    with charm.provide_charm_instance() as ovn_charm:
        ovn_charm.upgrade_if_available([ovsdb_peer])
        ovn_charm.assess_status()


@reactive.when_none('is-update-status-hook',
                    'coordinator.granted.upgrade',
                    'coordinator.requested.upgrade',
                    'config.changed.source',
                    'config.changed.ovn-source',
                    'endpoint.ovsdb-peer.departed')
@reactive.when('ovsdb-peer.available',
               'leadership.set.nb_cid',
               'leadership.set.sb_cid',
               'certificates.connected',
               'certificates.available')
def render():
    ovsdb = reactive.endpoint_from_name('ovsdb')
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
        ovn_charm.join_cluster('ovnnb_db.db', 'OVN_Northbound',
                               ovsdb_peer.db_connection_strs(
                                   (ovsdb_peer.cluster_local_addr,),
                                   ovsdb_peer.db_nb_cluster_port),
                               ovsdb_peer.db_connection_strs(
                                   ovsdb_peer.cluster_remote_addrs,
                                   ovsdb_peer.db_nb_cluster_port))
        ovn_charm.join_cluster('ovnsb_db.db', 'OVN_Southbound',
                               ovsdb_peer.db_connection_strs(
                                   (ovsdb_peer.cluster_local_addr,),
                                   ovsdb_peer.db_sb_cluster_port),
                               ovsdb_peer.db_connection_strs(
                                   ovsdb_peer.cluster_remote_addrs,
                                   ovsdb_peer.db_sb_cluster_port))
        if ovn_charm.enable_services():
            # Handle any post deploy configuration changes impacting listeners
            ovn_charm.configure_ovn(
                ovsdb_peer.db_nb_port,
                ovsdb.db_sb_port,
                ovsdb_peer.db_sb_admin_port)
            reactive.set_flag('config.rendered')
        ovn_charm.assess_status()


@reactive.when_none('charm.paused', 'is-update-status-hook')
@reactive.when('config.rendered')
@reactive.when_any('config.changed.nagios_context',
                   'config.changed.nagios_servicegroups',
                   'endpoint.nrpe-external-master.changed',
                   'nrpe-external-master.available')
def configure_nrpe():
    """Handle config-changed for NRPE options."""
    with charm.provide_charm_instance() as charm_instance:
        charm_instance.render_nrpe()


@reactive.when_not('is-update-status-hook')
def configure_deferred_restarts():
    with charm.provide_charm_instance() as instance:
        instance.configure_deferred_restarts()


@reactive.when('is-update-status-hook')
def check_ovn_certs():
    with charm.provide_charm_instance() as charm_instance:
        charm_instance.check_ovn_certs()


@reactive.when_none('is-update-status-hook')
@reactive.when_any('config.changed.ovn-exporter-channel',
                   'snap.installed.prometheus-ovn-exporter')
def reassess_exporter():
    with charm.provide_charm_instance() as instance:
        instance.assess_exporter()


@reactive.when_none('is-update-status-hook')
@reactive.when('charm.installed',
               'metrics-endpoint.available',
               'snap.installed.prometheus-ovn-exporter')
def handle_metrics_endpoint():
    metrics_endpoint = reactive.endpoint_from_flag(
        'metrics-endpoint.available')
    job_name = 'ovn-exporter'
    metrics_endpoint.expose_job(
        job_name,
        static_configs=[{"targets": ["*:9476"]}])


@reactive.when_none('is-update-status-hook')
@reactive.when('charm.installed', 'metrics-endpoint.available')
@reactive.when_not('snap.installed.prometheus-ovn-exporter')
def maybe_clear_metrics_endpoint():
    """Clear the metrics endpoint state if the exporter isn't installed.

    An operator may choose not to install the ovs exporter which needs
    to be reflected if a relation to prometheus is present to avoid
    scrape errors.
    """
    metrics_endpoint = reactive.endpoint_from_flag(
        'metrics-endpoint.available')
    job_name = 'ovn-exporter'
    if not reactive.is_flag_set(f'metrics-endpoint.exposed.{job_name}'):
        return

    metrics_endpoint.clear_job(job_name)


@reactive.when('endpoint.ovsdb-peer.departed')
def handle_cluster_downscale():
    """Handle OVN cluster's downscaling when unit is removed.

    There are two branches of code in this function. If it's executed on a
    unit that is being removed, It should trigger "cluster/leave" message.
    If, on the other hand, this code is executed on a unit that's remaining,
    it should wait before the departing unit can send out the "cluster/leave"
    command before reconfiguring firewall and closing off ports.
    """
    if reactive.is_flag_set("ovsdb-peer.left_cluster"):
        # Departing unit already left cluster
        hookenv.log("Servers already left the cluster.", hookenv.INFO)
        return

    departing_unit = hookenv.departing_unit()
    is_departing_unit = hookenv.local_unit() == departing_unit

    if is_departing_unit:
        # Departing unit must attempt to gracefully leave OVN cluster.
        with charm.provide_charm_instance() as ovn:
            ovn.leave_cluster()

        reactive.set_flag("ovsdb-peer.left_cluster")
    else:
        # unit that remains in cluster should wait for departing unit to
        # gracefully leave cluster before reconfiguring firewall
        peers = reactive.endpoint_from_name("ovsdb-peer")
        remote_unit_ip = peers.all_departed_units[
            departing_unit
        ].received["bound-address"]

        with charm.provide_charm_instance() as ovn:
            departed = ovn.wait_for_server_leave(remote_unit_ip)

        if departed:
            hookenv.log(
                "Departing unit {} successfully disconnected from "
                "cluster.".format(departing_unit),
                hookenv.INFO
            )
        else:
            hookenv.log(
                "Departing unit {} failed to remove itself from cluster. "
                "Please use action `cluster-kick` to remove straggling "
                "servers from OVN cluster.".format(departing_unit),
                hookenv.WARNING
            )
        configure_firewall()
