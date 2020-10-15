#!/usr/bin/env python3
"""Nagios plugin for OVN status."""

import argparse
import os
import subprocess

from nagios_plugin3 import CriticalError, UnknownError, try_check


class NRPEBase:
    """Base class for NRPE checks."""

    def __init__(self, args):
        """Init base class."""
        self.args = args
        self.db = args.db

    @property
    def cmds(self):
        """Determine which command to use for checks."""
        # Check for version based on socket location

        if os.path.exists("/var/run/ovn/ovnsb_db.ctl"):
            commands = {
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
        elif os.path.exists("/var/run/openvswitch/ovnsb_db.ctl"):
            commands = {
                "nb": [
                    "sudo",
                    "/usr/bin/ovs-appctl",
                    "-t",
                    "/var/run/openvswitch/ovnnb_db.ctl",
                    "cluster/status",
                    "OVN_Northbound",
                ],
                "sb": [
                    "sudo",
                    "/usr/bin/ovs-appctl",
                    "-t",
                    "/var/run/openvswitch/ovnsb_db.ctl",
                    "cluster/status",
                    "OVN_Southbound",
                ],
            }
        else:
            raise UnknownError("UNKNOWN: Socket for OVN database "
                               "does not exist")

        return commands

    def get_db_status(self):
        """Query the requested database for state."""
        status_output = self._run_command(self.cmds[self.db])
        status = self._parse_status_output(status_output)

        if status["Status"] != "cluster member":
            raise CriticalError(
                "CRITICAL: cluster status for {} db is {}".format(
                    self.db, status["Status"]
                )
            )
        # TODO, check for growth in key "Term"
        # TODO, review 'Entries not yet committed'

        return True

    def _run_command(self, cmd):
        """Run a command, and return it's result."""
        try:
            output = subprocess.check_output(cmd).decode("UTF-8")
        except (subprocess.CalledProcessError, FileNotFoundError) as error:
            msg = "CRITICAL: {} failed: {}".format(
                " ".join(cmd), error
            )
            raise CriticalError(msg)

            return False

        return output

    def _parse_status_output(self, status_output):
        """Parse output from database status query."""
        lines = status_output.split("\n")
        status = {}
        # Crude split by first colon

        for line in lines:
            if ":" in line:
                (key, value) = line.split(":", 1)
                status[key] = value.strip()

        return status


def collect_args():
    """Parse provided arguments."""
    parser = argparse.ArgumentParser(
        description="NRPE check for OVN database state"
    )
    parser.add_argument(
        "--db",
        help="Which database to check, Northbound (nb) or Southbound (sb). "
        "Defaults to nb.",
        choices=["nb", "sb"],
        default="sb",
        type=str,
    )

    args = parser.parse_args()

    return args


def main():
    """Define main subroutine."""
    args = collect_args()
    nrpe_check = NRPEBase(args)

    try_check(nrpe_check.get_db_status)

    # If we got here, everything is good
    print("OK: OVN {} database is OK".format(args.db))


if __name__ == "__main__":
    main()
