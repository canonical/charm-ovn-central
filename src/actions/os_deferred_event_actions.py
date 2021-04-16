#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd
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
import sys

# Load modules from $CHARM_DIR/lib
sys.path.append('lib')

from charms.layer import basic
basic.bootstrap_charm_deps()

import charmhelpers.contrib.openstack.deferred_events as deferred_events
import charmhelpers.contrib.openstack.utils as os_utils
import charmhelpers.core.hookenv as hookenv
import charms_openstack.bus
import charms_openstack.charm
import charms.reactive as reactive

charms_openstack.bus.discover()


def handle_package_updates():
    """Ensure DB services are restarted PKG update."""
    for event in deferred_events.get_deferred_events():
        if (event.reason == 'Package update' and
                event.service.startswith('ovn-central')):
            os_utils.restart_services_action(services=[
                'ovn-central',
                'ovn-northd',
                'ovn-ovsdb-server-nb',
                'ovn-ovsdb-server-sb'])


def restart_services(args):
    """Restart services.

    :param args: Unused
    :type args: List[str]
    """
    deferred_only = hookenv.action_get("deferred-only")
    services = hookenv.action_get("services").split()
    # Check input
    if deferred_only and services:
        hookenv.action_fail("Cannot set deferred-only and services")
        return
    if not (deferred_only or services):
        hookenv.action_fail("Please specify deferred-only or services")
        return
    if deferred_only:
        handle_package_updates()
        os_utils.restart_services_action(deferred_only=True)
    else:
        os_utils.restart_services_action(services=services)
    with charms_openstack.charm.provide_charm_instance() as charm_instance:
        charm_instance._assess_status()


def show_deferred_events(args):
    """Show the deferred events.

    :param args: Unused
    :type args: List[str]
    """
    os_utils.show_deferred_events_action_helper()


def run_deferred_hooks(args):
    """Run deferred hooks.

    :param args: Unused
    :type args: List[str]
    """
    deferred_methods = deferred_events.get_deferred_hooks()
    ovsdb = reactive.endpoint_from_flag('ovsdb.available')
    with charms_openstack.charm.provide_charm_instance() as charm_instance:
        if ('install' in deferred_methods or
                'configure_ovs' in deferred_methods):
            charm_instance.install(check_deferred_events=False)
        if 'configure_ovs' in deferred_methods:
            charm_instance.render_with_interfaces(
                charms_openstack.charm.optional_interfaces(
                    (ovsdb,),
                    'nova-compute.connected',
                    'amqp.connected'))
            charm_instance.configure_ovs(
                ','.join(ovsdb.db_sb_connection_strs),
                reactive.is_flag_set('config.changed.disable-mlockall'),
                check_deferred_events=False)
        charm_instance._assess_status()


# Actions to function mapping, to allow for illegal python action names that
# can map to a python function.
ACTIONS = {
    "restart-services": restart_services,
    "show-deferred-events": show_deferred_events,
    "run-deferred-hooks": run_deferred_hooks
}


def main(args):
    hookenv._run_atstart()
    action_name = os.path.basename(args[0])
    try:
        action = ACTIONS[action_name]
    except KeyError:
        return "Action %s undefined" % action_name
    else:
        try:
            action(args)
        except Exception as e:
            hookenv.action_fail(str(e))
    hookenv._run_atexit()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
