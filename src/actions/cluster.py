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

import subprocess

# Load modules from $CHARM_DIR/lib
sys.path.append("lib")

from charms.layer import basic

basic.bootstrap_charm_deps()

import charms_openstack.bus
import charms_openstack.charm
import charms.reactive as reactive
import charmhelpers.core as ch_core
import charmhelpers.contrib.network.ovs.ovn as ch_ovn
import charmhelpers.contrib.network.ip as ch_ip

charms_openstack.bus.discover()


class StatusParsingException(Exception):
    """Exception when OVN cluster status has unexpected format/values."""


def _url_to_ip(cluster_url):
    """Parse IP from cluster URL.

    OVN cluster uses urls like "ssl:10.0.0.1:6644". This function parses the
    IP portion out of the url. This function works with IPv4 and IPv6
    addresses.

    :raises StatusParsingException: If cluster_url does not contain valid IP
        address.
    :param cluster_url: OVN server url. Like "ssl:10.0.0.1".
    :type cluster_url: str
    :return: Parsed out IP address
    :rtype: str
    """
    ip_portion = cluster_url.split(":")[1:-1]
    if len(ip_portion) > 1:
        # Possible IPv6 address
        ip_str = ":".join(ip_portion)
    else:
        # Likely a IPv4 address
        ip_str = "".join(ip_portion)

    if not ch_ip.is_ip(ip_str):
        raise StatusParsingException(
            "Failed to parse OVN cluster status. Cluster member address "
            "has unexpected format: {}".format(cluster_url)
        )

    return ip_str


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
    :type raw_cluster_status: ch_ovn.OVNClusterStatus
    :param cluster_ip_map: mapping between juju units and their IPs in the
        cluster.
    :type cluster_ip_map: dict
    :return: Cluster status in the form of dictionary
    :rtype: dict
    """
    mapped_servers = {}
    unknown_servers = []

    #  Map unit name to each server in the Servers field.
    for server_id, server_url in raw_cluster_status.servers:
        member_address = _url_to_ip(server_url)
        for unit, ip in cluster_ip_map.items():
            if member_address == ip:
                mapped_servers[unit] = server_id
                break
        else:
            unknown_servers.append(server_id)

    cluster = raw_cluster_status.to_yaml()

    if unknown_servers:
        mapped_servers["UNKNOWN"] = unknown_servers
    cluster["unit_map"] = mapped_servers

    return cluster


def _cluster_ip_map():
    """Produce mapping between units and their IPs.

    This function selects an IP bound to the ovsdb-peer endpoint.

    Example output: {"ovn-central/0": "10.0.0.1", ...}
    """
    # Existence of ovsdb-peer relation is guaranteed by check in the main func
    ovsdb_peers = reactive.endpoint_from_flag("ovsdb-peer.available")
    local_unit_id = ch_core.hookenv.local_unit()
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


def _kick_server(cluster, server_id):
    """Perform ovn-appctl cluster/kick to remove server from selected cluster.

    :raises:
        subprocess.CalledProcessError: If subprocess command execution fails.
        ValueError: If cluster parameter doesn't have an expected value.
    :param cluster: Cluster from which the server should be kicked. Available
        options are "northbound" or "southbound"
    :type cluster: str
    :param server_id: short ID of a server to be kicked
    :type server_id: str
    :return: None
    """
    if cluster.lower() == "southbound":
        params = ("ovnsb_db", ("cluster/kick", "OVN_Southbound", server_id))
    elif cluster.lower() == "northbound":
        params = ("ovnnb_db", ("cluster/kick", "OVN_Northbound", server_id))
    else:
        raise ValueError(
            "Unexpected value of 'cluster' parameter: '{}'".format(cluster)
        )
    ch_ovn.ovn_appctl(*params)


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
        ch_core.hookenv.action_fail(str(exc))
        return

    ch_core.hookenv.action_set(
        {"ovnsb": yaml.safe_dump(sb_cluster, sort_keys=False)}
    )
    ch_core.hookenv.action_set(
        {"ovnnb": yaml.safe_dump(nb_cluster, sort_keys=False)}
    )


def cluster_kick():
    """Implementation of a "cluster-kick" action."""
    sb_server_id = str(ch_core.hookenv.action_get("sb-server-id"))
    nb_server_id = str(ch_core.hookenv.action_get("nb-server-id"))

    if not (sb_server_id or nb_server_id):
        ch_core.hookenv.action_fail(
            "At least one server ID to kick must be specified."
        )
        return

    if sb_server_id:
        try:
            _kick_server("southbound", sb_server_id)
            ch_core.hookenv.action_set(
                {"ovnsb": "requested kick of {}".format(sb_server_id)}
            )
        except subprocess.CalledProcessError as exc:
            ch_core.hookenv.action_fail(
                "Failed to kick Southbound cluster member "
                "{}: {}".format(sb_server_id, exc.output)
            )

    if nb_server_id:
        try:
            _kick_server("northbound", nb_server_id)
            ch_core.hookenv.action_set(
                {"ovnnb": "requested kick of {}".format(nb_server_id)}
            )
        except subprocess.CalledProcessError as exc:
            ch_core.hookenv.action_fail(
                "Failed to kick Northbound cluster member "
                "{}: {}".format(nb_server_id, exc.output)
            )


ACTIONS = {"cluster-status": cluster_status, "cluster-kick": cluster_kick}


def main(args):
    ch_core.hookenv._run_atstart()
    #  Abort action if this unit is not in a cluster.
    if reactive.endpoint_from_flag("ovsdb-peer.available") is None:
        ch_core.hookenv.action_fail("Unit is not part of an OVN cluster.")
        return

    action_name = os.path.basename(args[0])
    try:
        action = ACTIONS[action_name]
    except KeyError:
        return "Action %s undefined" % action_name
    else:
        try:
            action()
        except Exception as e:
            ch_core.hookenv.action_fail(str(e))
    ch_core.hookenv._run_atexit()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
