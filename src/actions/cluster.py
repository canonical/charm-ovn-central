#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd
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

import yaml

from subprocess import CalledProcessError

# Load modules from $CHARM_DIR/lib
sys.path.append("lib")

from charms.layer import basic

basic.bootstrap_charm_deps()

import charms_openstack.bus
import charms_openstack.charm
import charms.reactive as reactive
from charmhelpers.core import hookenv
from charmhelpers.contrib.network.ovs.ovn import ovn_appctl

charms_openstack.bus.discover()


class StatusParsingException(Exception):
    """Exception when OVN cluster status has unexpected format/values."""


def _format_cluster_status(raw_cluster_status, cluster_ip_map):
    """Reformat cluster status into dict.

    Resulting dictionary also includes mapping between cluster servers and
    juju units.

    Parameter cluster_ip_map is a dictionary with juju unit IDs as a key and
    their respective IP addresses as a value. Example:

        {"ovn-central/0": "10.0.0.1", "ovn-central/1: "10.0.0.2"}

    :raises StatusParsingException: In case the parsing of a cluster status
        fails.

    :param raw_cluster_status: Cluster status object
    :type raw_cluster_status:
        charmhelpers.contrib.network.ovs.ovn.OVNClusterStatus
    :param cluster_ip_map: mapping between juju units and their IPs in the
        cluster.
    :type cluster_ip_map: dict
    :return: Cluster status in the form of dictionary
    :rtype: dict
    """
    cluster = {
        "Cluster ID": str(raw_cluster_status.cluster_id),
        "Server ID": str(raw_cluster_status.server_id),
        "Address": raw_cluster_status.address,
        "Status": raw_cluster_status.status,
        "Role": raw_cluster_status.role,
        "Term": raw_cluster_status.term,
        "Leader": raw_cluster_status.leader,
        "Vote": raw_cluster_status.vote,
        "Log": raw_cluster_status.log,
        "Entries not yet committed":
            raw_cluster_status.entries_not_yet_committed,
        "Entries not yet applied":
            raw_cluster_status.entries_not_yet_applied,
    }
    mapped_servers = {}

    #  Add unit name to each server in the Servers field.
    for server_id, server_url in raw_cluster_status.servers:
        mapped_servers[server_id] = {"Address": server_url}
        parsed_url = server_url.split(":")
        if len(parsed_url) != 3:
            #  server address did not have expected format ssl:<IP>:<PORT>
            raise StatusParsingException(
                "Failed to parse OVN cluster status. Cluster member address "
                "has unexpected format: %s" % server_url
            )
        member_address = parsed_url[1]
        for unit, ip in cluster_ip_map.items():
            if member_address == ip:
                mapped_servers[server_id]["Unit"] = unit
                break
        else:
            mapped_servers[server_id]["Unit"] = "UNKNOWN"

    cluster["Servers"] = mapped_servers

    return cluster


def _cluster_ip_map():
    """Produce mapping between units and their IPs.

    This function selects an IP bound to the ovsdb-peer endpoint.

    Example output: {"ovn-central/0": "10.0.0.1", ...}
    """
    ovsdb_peers = reactive.endpoint_from_flag("ovsdb-peer.available")
    local_unit_id = hookenv.local_unit()
    local_ip = ovsdb_peers.cluster_local_addr
    unit_map = {local_unit_id: local_ip}

    for relation in ovsdb_peers.relations:
        for unit in relation.units:
            try:
                address = unit.received.get("bound-address", "")
                unit_map[unit.unit_name] = address
            except ValueError:
                pass

    return unit_map


def cluster_status():
    """Implementation of a "cluster-status" action."""
    with charms_openstack.charm.provide_charm_instance() as charm_instance:
        sb_status = charm_instance.cluster_status("ovnsb_db")
        nb_status = charm_instance.cluster_status("ovnnb_db")

    try:
        unit_ip_map = _cluster_ip_map()
        sb_cluster = _format_cluster_status(sb_status, unit_ip_map)
        nb_cluster = _format_cluster_status(nb_status, unit_ip_map)
    except StatusParsingException as exc:
        hookenv.function_fail(str(exc))
        return

    hookenv.function_set(
        {"southbound-cluster": yaml.dump(sb_cluster, sort_keys=False)}
    )
    hookenv.function_set(
        {"northbound-cluster": yaml.dump(nb_cluster, sort_keys=False)}
    )


def kick_server():
    """Implementation of a "cluster-kick" action."""
    sb_server_id = str(hookenv.function_get("sb-server-id"))
    nb_server_id = str(hookenv.function_get("nb-server-id"))

    if not (sb_server_id or nb_server_id):
        hookenv.function_fail(
            "At least one server ID to kick must be specified."
        )
        return

    if sb_server_id:
        try:
            ovn_appctl(
                "ovnsb_db", ("cluster/kick", "OVN_Southbound", sb_server_id)
            )
            hookenv.function_set(
                {"southbound": "requested kick of {}".format(sb_server_id)}
            )
        except CalledProcessError as exc:
            hookenv.function_fail(
                "Failed to kick Southbound cluster member "
                "{}: {}".format(sb_server_id, exc.output)
            )

    if nb_server_id:
        try:
            ovn_appctl(
                "ovnnb_db", ("cluster/kick", "OVN_Northbound", nb_server_id)
            )
            hookenv.function_set(
                {"northbound": "requested kick of {}".format(nb_server_id)}
            )
        except CalledProcessError as exc:
            hookenv.function_fail(
                "Failed to kick Northbound cluster member "
                "{}: {}".format(nb_server_id, exc.output)
            )


ACTIONS = {"cluster-status": cluster_status, "cluster-kick": kick_server}


def main(args):
    hookenv._run_atstart()
    #  Abort action if this unit is not in a cluster.
    if reactive.endpoint_from_flag("ovsdb-peer.available") is None:
        hookenv.function_fail("Unit is not part of an OVN cluster.")
        return

    action_name = os.path.basename(args[0])
    try:
        action = ACTIONS[action_name]
    except KeyError:
        return "Action %s undefined" % action_name
    else:
        try:
            print(action)
            action()
        except Exception as e:
            hookenv.function_fail(str(e))
    hookenv._run_atexit()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
