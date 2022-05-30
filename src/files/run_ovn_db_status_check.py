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

OUTPUT_FILE = {'sb': "/var/lib/nagios/ovn_sb_db_status.out",
               'nb': "/var/lib/nagios/ovn_nb_db_status.out"}

COMMAND = {
    "nb": [
        "sudo",
        "/usr/bin/ovn-appctl",
        "-t",
        "/var/run/ovn/ovnnb_db.ctl",
        "cluster/status",
        "OVN_Northbound",
    ],
    "sb": [
        "sudo",
        "/usr/bin/ovn-appctl",
        "-t",
        "/var/run/ovn/ovnsb_db.ctl",
        "cluster/status",
        "OVN_Southbound",
    ],
}

Alert = namedtuple("Alert", "status msg")


def get_db_status(formatted_output, db):
    """Query the requested database for state."""
    name = formatted_output["Name"]
    status = formatted_output["Status"]
    server_id = formatted_output["Server ID"]

    if status in ["joining cluster", "leaving cluster", "left cluster"]:
        return Alert(
            NAGIOS_STATUS_WARNING, "status for {} in {} db is {}".format(
                server_id, name, status
            )
        )
    if status in ["failed",
                  "disconnected from the cluster (election timeout)"]:
        return Alert(
            NAGIOS_STATUS_CRITICAL, "status for {} in {} db is {}".format(
                server_id, name, status
            )
        )

    return Alert(
        NAGIOS_STATUS_OK, "status for {} in {} db is {}".format(
            server_id, name, status
        )
    )


def parse_output(output):
    """Parse output from database status query."""
    lines = output.split("\n")
    status = {}
    # Crude split by first colon

    for line in lines:
        if ":" in line:
            (key, value) = line.split(":", 1)
            status[key] = value.strip()

    return status


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


def aggregate_alerts(alerts, formatted_output):
    """Reduce results down to an overall single status based on the highest
    level."""
    msg_crit = []
    msg_warn = []
    msg_ok = []

    name = formatted_output["Name"]
    role = formatted_output["Role"]
    server_id = formatted_output["Server ID"]

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
        status_detail = "{} DB {} {} status is normal".format(
            name, role, server_id
        )

    return "{}: {}".format(severity, status_detail)


def run_checks(db):
    """Check health of OVN SB DB connections."""
    output = "UNKNOWN"
    alerts = []
    try:
        cmd_output = check_output(COMMAND[db]).decode("utf-8")
        formatted_output = parse_output(cmd_output)
        alerts.append(get_db_status(formatted_output, db))
        output = aggregate_alerts(alerts, formatted_output)
    except CalledProcessError as error:
        output = "UKNOWN: {}".format(error.stdout.decode(errors="ignore"))

    write_output_file(output, OUTPUT_FILE[db])


def main():
    run_checks("sb")
    run_checks("nb")


if __name__ == "__main__":
    main()
