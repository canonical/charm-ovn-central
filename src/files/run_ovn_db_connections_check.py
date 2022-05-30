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

OVN_SB_DB_CTL = "/var/run/ovn/ovnsb_db.ctl"

OUTPUT_FILE = {'sb': "/var/lib/nagios/ovn_sb_db_connections.out",
               'nb': "/var/lib/nagios/ovn_nb_db_connections.out"}

EXPECTED_CONNECTIONS = {'sb': 2, 'nb': 1}

Alert = namedtuple("Alert", "status msg")


def get_uuid(connection):
    """Retreive UUID from OVN DB connection JSON."""
    return connection["_uuid"][1]


def check_role_target(connection, db):
    """Validate OVN connection target and role fields."""
    uuid = get_uuid(connection)

    if db == "sb":
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
                "{}: target pssl:16642 has role {} but expected \"\"".format(
                    uuid, connection["role"]
                ),
            )
    else:
        if connection["target"] != "pssl:6641":
            return Alert(
                NAGIOS_STATUS_CRITICAL,
                "{}: unexpected target: {}".format(uuid, connection["target"]),
            )

    return Alert(NAGIOS_STATUS_OK, "{}: target and role are OK".format(uuid))


def check_read_only(connection):
    """Ensure that OVN DB connection isn't in read_only state."""
    uuid = get_uuid(connection)
    if connection["read_only"]:
        return Alert(
            NAGIOS_STATUS_CRITICAL, "{}: connection is read only".format(uuid)
        )
    return Alert(
        NAGIOS_STATUS_OK, "{}: connection is not read_only".format(uuid)
    )


def check_connections(connections, db):
    """Run checks against OVN DB connections."""
    alerts = []
    controllers_count = 0

    if len(connections) != EXPECTED_CONNECTIONS[db]:
        alerts.append(
            Alert(
                NAGIOS_STATUS_CRITICAL,
                "expected {} connections, got {}".format(
                    EXPECTED_CONNECTIONS[db],
                    len(connections)
                ),
            )
        )

    for conn in connections:
        alerts.append(check_role_target(conn, db))

        if db == "sb":
            if conn["role"] == "ovn-controller":
                controllers_count += 1
            alerts.append(check_read_only(conn))

    # assert that exactly 1 controller connection exists
    if db == "sb" and controllers_count != 1:
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


def write_output_file(output, output_file):
    """Write results of checks to the defined location for nagios to check."""
    tmp_output_file = output_file + ".tmp"
    try:
        with open(tmp_output_file, "w") as tmp_file:
            tmp_file.write(output)
    except IOError as err:
        print(
            "Cannot write output file {}, error {}".format(
                tmp_output_file, err
            )
        )
        sys.exit(1)
    os.rename(tmp_output_file, output_file)


def is_leader():
    """Check whether the current unit is OVN Southbound DB leader."""
    cmd = [
        "ovs-appctl",
        "-t",
        OVN_SB_DB_CTL,
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


def aggregate_alerts(alerts, db):
    """Reduce results down to an overall single status based on the highest
    level."""
    msg_crit = []
    msg_warn = []
    msg_ok = []

    for alert in alerts:
        if alert.status == NAGIOS_STATUS_CRITICAL:
            msg_crit.append(alert.msg)
        elif alert.status == NAGIOS_STATUS_WARNING:
            msg_warn.append(alert.msg)
        else:
            msg_ok.append(alert.msg)

    severity = "OK"
    status_detail = ""

    if len(msg_crit) > 0:
        severity = "CRITICAL"
        status_detail = "; ".join(
            filter(
                None,
                [
                    status_detail,
                    "critical[{}]: {}".format(len(msg_crit), msg_crit),
                ],
            )
        )
    if len(msg_warn) > 0:
        if severity != "CRITICAL":
            severity = "WARNING"
        status_detail = "; ".join(
            filter(
                None,
                [
                    status_detail,
                    "warnings[{}]: {}".format(len(msg_warn), msg_warn),
                ],
            )
        )
    if len(msg_crit) == 0 and len(msg_warn) == 0:
        status_detail = "OVN {} DB connections are normal".format(db.upper())

    return "{}: {}".format(severity, status_detail)


def run_checks(db):
    """Check health of both SB and NB DB connections for OVN."""
    output = "UNKNOWN"
    try:
        if is_leader():
            cmd = ["ovn-{}ctl".format(db),
                   "--format=json",
                   "list",
                   "connection"]
            cmd_output = check_output(cmd).decode("utf-8")
            connections = parse_output(cmd_output)
            alerts = check_connections(connections, db)
            output = aggregate_alerts(alerts, db)
        else:
            output = "OK: no-op (unit is not the DB leader)"
    except CalledProcessError as error:
        output = "UKNOWN: {}".format(error.stdout.decode(errors="ignore"))

    write_output_file(output, OUTPUT_FILE[db])


def main():
    run_checks("sb")
    run_checks("nb")


if __name__ == "__main__":
    main()
