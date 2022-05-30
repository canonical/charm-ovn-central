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
"""
This script checks the output of 'ovn-sbctl list connections' for error
conditions.
"""

import sys
import os
import json
from collections import namedtuple
from subprocess import check_output, CalledProcessError

NAGIOS_STATUS_OK = 0
NAGIOS_STATUS_WARNING = 1
NAGIOS_STATUS_CRITICAL = 2
NAGIOS_STATUS_UNKNOWN = 3

NAGIOS_STATUS = {
    NAGIOS_STATUS_OK: "OK",
    NAGIOS_STATUS_WARNING: "WARNING",
    NAGIOS_STATUS_CRITICAL: "CRITICAL",
    NAGIOS_STATUS_UNKNOWN: "UNKNOWN",
}

OUTPUT_FILE = "/var/lib/nagios/ovn_db_connections.out"
OVNSB_DB_CTL = "/var/run/ovn/ovnsb_db.ctl"
TMP_OUTPUT_FILE = OUTPUT_FILE + ".tmp"

EXPECTED_CONNECTIONS = 2

Alert = namedtuple("Alert", "status msg")


def get_uuid(connection):
    """Retreive UUID from OVN DB connection JSON."""
    return connection["_uuid"][1]


def check_role_target(connection):
    """Validate OVN connection target and role fields."""
    uuid = get_uuid(connection)

    if connection["target"] not in ["pssl:6642", "pssl:16642"]:
        return Alert(
            NAGIOS_STATUS_CRITICAL,
            "{}: unexpected target: {}".format(uuid, connection["target"]),
        )

    if connection["role"] not in ["ovn-controller", ""]:
        return Alert(
            NAGIOS_STATUS_CRITICAL,
            "{}: unexpected role: {}".format(uuid, connection["role"]),
        )

    if connection["target"] == "pssl:6642" and connection["role"] == "":
        return Alert(
            NAGIOS_STATUS_WARNING, "{}: RBAC is disabled".format(uuid)
        )

    if connection["target"] == "pssl:16642" and connection["role"] != "":
        return Alert(
            NAGIOS_STATUS_CRITICAL,
            "{}: target pssl:16642 should not be used by role {}".format(
                uuid, connection["role"]
            ),
        )

    return Alert(NAGIOS_STATUS_OK, "{}: target and role are OK".format(uuid))


def check_read_only(connection):
    """Ensure that OVN DB connection isn't in read_only state."""
    uuid = get_uuid(connection)
    if connection["read_only"] is not False:
        return Alert(
            NAGIOS_STATUS_CRITICAL, "{}: connection is read only".format(uuid)
        )
    return Alert(
        NAGIOS_STATUS_OK, "{}: connection is not read_only".format(uuid)
    )


def check_connection_status(connection):
    """Ensure that OVN DB connection status is normal (empty list)."""
    uuid = get_uuid(connection)
    status = connection["status"][1]
    if len(status) != 0:
        return Alert(
            NAGIOS_STATUS_WARNING, "{}:" +
            " unexpected connection status - {}".format(uuid, status)
        )
    return Alert(
        NAGIOS_STATUS_OK, "{}: connection status is normal".format(uuid)
    )


def check_connections(connections):
    """Run checks against OVN DB connections."""
    alerts = []
    controllers_count = 0

    if len(connections) != EXPECTED_CONNECTIONS:
        alerts.append(
            Alert(
                NAGIOS_STATUS_CRITICAL,
                "expected 2 connections, got {}".format(len(connections)),
            )
        )

    for conn in connections:
        if conn["role"] == "ovn-controller":
            controllers_count += 1
        alerts.append(check_role_target(conn))
        alerts.append(check_read_only(conn))
        alerts.append(check_connection_status(conn))

    # assert that exactly 1 controller connection exists
    if controllers_count != 1:
        alerts.append(
            Alert(
                NAGIOS_STATUS_CRITICAL,
                "expected 1 ovn-controller connection, got {}".format(
                    controllers_count
                ),
            )
        )

    return alerts


def parse_output(raw):
    """Parses output of ovnsb-ctl"""
    status = json.loads(raw)
    data = status["data"]
    headings = status["headings"]
    connections = []
    for connection_data in data:
        connections.append(dict(zip(headings, connection_data)))
    return connections


def write_output_file(output):
    """Write results of checks to the defined location for nagios to check."""
    try:
        with open(TMP_OUTPUT_FILE, "w") as output_file:
            output_file.write(output)
    except IOError as err:
        print(
            "Cannot write output file {}, error {}".format(
                TMP_OUTPUT_FILE, err
            )
        )
        sys.exit(1)
    os.rename(TMP_OUTPUT_FILE, OUTPUT_FILE)


def is_leader():
    """Check whether the current unit is OVN Southbound DB leader."""
    cmd = [
        "ovs-appctl",
        "-t",
        OVNSB_DB_CTL,
        "cluster/status",
        "OVN_Southbound",
    ]
    output = check_output(cmd).decode("utf-8")

    output_lines = output.split("\n")
    role_line = [line for line in output_lines if line.startswith("Role:")]

    if len(role_line) > 0:
        _, role = role_line[0].split(":")
        return role.strip() == "leader"

    print("'Role:' line not found in the output of '{}'".format(" ".join(cmd)))
    return False


def aggregate_alerts(alerts):
    """Reduce results down to an overall single status based on the highest
    level."""
    total_crit = 0
    total_warn = 0

    msg_crit = []
    msg_warn = []
    msg_ok = []

    for alert in alerts:
        if alert.status == NAGIOS_STATUS_CRITICAL:
            total_crit += 1
            msg_crit.append(alert.msg)
        elif alert.status == NAGIOS_STATUS_WARNING:
            total_warn += 1
            msg_warn.append(alert.msg)
        else:
            msg_ok.append(alert.msg)

    severity = "OK"
    status_detail = ""

    if total_crit > 0:
        severity = "CRITICAL"
        status_detail = "; ".join(
            filter(
                None,
                [
                    status_detail,
                    "critical[{}]: {}".format(total_crit, msg_crit),
                ],
            )
        )
    if total_warn > 0:
        if severity != "CRITICAL":
            severity = "WARNING"
        status_detail = "; ".join(
            filter(
                None,
                [
                    status_detail,
                    "warnings[{}]: {}".format(total_warn, msg_warn),
                ],
            )
        )
    if total_crit == 0 and total_warn == 0:
        status_detail = "OVN DB connections are normal"

    return "{}: {}".format(severity, status_detail)


def run_checks():
    """Check health of OVN SB DB connections."""
    output = "UNKNOWN"
    try:
        if is_leader():
            cmd = ["ovn-sbctl", "--format=json", "list", "connection"]
            cmd_output = check_output(cmd).decode("utf-8")
            connections = parse_output(cmd_output)
            alerts = check_connections(connections)
            output = aggregate_alerts(alerts)
        else:
            output = "OK: no-op (unit is not the DB leader)"
    except CalledProcessError as error:
        output = "UKNOWN: {}".format(error.stdout.decode(errors="ignore"))

    write_output_file(output)


if __name__ == "__main__":
    run_checks()
