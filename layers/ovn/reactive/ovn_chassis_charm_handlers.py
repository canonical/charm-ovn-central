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
import os

import charmhelpers.core as ch_core

import charms.reactive as reactive

import charms_openstack.bus
import charms_openstack.charm as charm


charms_openstack.bus.discover()
OVN_CHASSIS_ENABLE_HANDLERS_FLAG = 'charm.ovn.chassis.enable-handlers'


@reactive.when(OVN_CHASSIS_ENABLE_HANDLERS_FLAG)
def enable_chassis_reactive_code():
    charm.use_defaults(
        'charm.installed',
        'config.changed',
        'config.rendered',
        'update-status',
        'upgrade-charm',
        'certificates.available',
    )


@reactive.when_none('charm.installed', 'charm.paused')
@reactive.when(OVN_CHASSIS_ENABLE_HANDLERS_FLAG, 'config.set.new-units-paused')
def pause_unit_from_config():
    with charm.provide_charm_instance() as instance:
        instance.pause()
        instance.assess_status()


# Note that RabbitMQ is only used for the Neutron SR-IOV agent
@reactive.when_none('charm.paused')
@reactive.when(OVN_CHASSIS_ENABLE_HANDLERS_FLAG, 'amqp.connected')
def amqp_connection():
    amqp = reactive.endpoint_from_flag('amqp.connected')
    with charm.provide_charm_instance() as instance:
        amqp.request_access(username='neutron', vhost='openstack')
        instance.assess_status()


@reactive.when_none('charm.paused', 'nova-compute.connected')
@reactive.when(OVN_CHASSIS_ENABLE_HANDLERS_FLAG)
def disable_openstack():
    reactive.clear_flag('charm.ovn-chassis.enable-openstack')


@reactive.when_none('charm.paused')
@reactive.when(OVN_CHASSIS_ENABLE_HANDLERS_FLAG, 'nova-compute.connected')
def enable_openstack():
    reactive.set_flag('charm.ovn-chassis.enable-openstack')
    nova_compute = reactive.endpoint_from_flag('nova-compute.connected')
    nova_compute.publish_shared_secret()
    with charm.provide_charm_instance() as charm_instance:
        charm_instance.install()
        charm_instance.assess_status()


@reactive.when_none('charm.paused')
@reactive.when(OVN_CHASSIS_ENABLE_HANDLERS_FLAG, 'config.rendered')
def configure_bridges():
    with charm.provide_charm_instance() as charm_instance:
        charm_instance.configure_bridges()
        charm_instance.assess_status()


@reactive.when_none('charm.paused')
@reactive.when(OVN_CHASSIS_ENABLE_HANDLERS_FLAG,
               'ovsdb.available',
               'certificates.available')
def configure_ovs():
    ovsdb = reactive.endpoint_from_flag('ovsdb.available')
    with charm.provide_charm_instance() as charm_instance:
        if reactive.is_flag_set('config.changed.enable-dpdk'):
            # Install required packages and/or run update-alternatives
            charm_instance.install()
        charm_instance.configure_ovs(','.join(ovsdb.db_sb_connection_strs))
        charm_instance.render_with_interfaces(
            charm.optional_interfaces((ovsdb,),
                                      'nova-compute.connected',
                                      'amqp.connected'))
        reactive.set_flag('config.rendered')
        charm_instance.assess_status()


@reactive.when_none('charm.paused', 'is-update-status-hook')
@reactive.when(OVN_CHASSIS_ENABLE_HANDLERS_FLAG, 'config.rendered')
@reactive.when_any('config.changed.nagios_context',
                   'config.changed.nagios_servicegroups',
                   'endpoint.nrpe-external-master.changed',
                   'nrpe-external-master.available')
def configure_nrpe():
    """Handle config-changed for NRPE options."""
    with charm.provide_charm_instance() as charm_instance:
        charm_instance.render_nrpe()
