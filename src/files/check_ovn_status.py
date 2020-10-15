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

        socket_paths = {"ovs": "/var/run/openvswitch", "ovn": "/var/run/ovn"}
        if os.path.exists(socket_paths["ovn"]):
            appctl_cmd = "/usr/bin/ovn-appctl"
            socket_path = socket_paths["ovn"]
        elif os.path.exists(socket_paths["ovs"]):
            appctl_cmd = "/usr/bin/ovs-appctl"
            socket_path = socket_paths["ovs"]
        else:
            raise UnknownError(
                "UNKNOWN: Path for OVN socket does not exist"
            )

        commands = {
            "nb": [
                "sudo",
                appctl_cmd,
                "-t",
                "{}/ovnnb_db.ctl".format(socket_path),
                "cluster/status",
                "OVN_Northbound",
            ],
            "sb": [
                "sudo",
                appctl_cmd,
                "-t",
                "{}/ovnsb_db.ctl".format(socket_path),
                "cluster/status",
                "OVN_Southbound",
            ],
        }

        controller_pidfile = "{}/ovn-controller.pid".format(socket_path)
        if os.path.exists(controller_pidfile):
            # the socket path contains the pid
            # TODO check what happens on Train
            with open(
                controller_pidfile, "r"
            ) as pidfile:
                pid = pidfile.read().rstrip()
            commands["controller"] = [
                "sudo",
                appctl_cmd,
                "-t",
                "{}/ovn-controller.{}.ctl".format(socket_path, pid),
                "connection-status",
            ]

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
            msg = "CRITICAL: {} failed: {}".format(" ".join(cmd), error)
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

    def get_controller_status(self):
        """Query the status of the ovn-controller socket."""
        status_output = self._run_command(self.cmds['controller']).rstrip()

        if status_output != "connected":
            raise CriticalError(
                "CRITICAL: OVN controller status is {}".format(status_output)
            )

        return True


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
        type=str,
    )
    parser.add_argument(
        "--controller",
        help="Check the ovn-controller status",
        action='store_true',
    )

    args = parser.parse_args()

    return args


def main():
    """Define main subroutine."""
    args = collect_args()
    nrpe_check = NRPEBase(args)

    if args.controller:
        try_check(nrpe_check.get_controller_status)

    if args.db:
        try_check(nrpe_check.get_db_status)

    # If we got here, everything is good
    print("OK: OVN process reports it is healthy.")


if __name__ == "__main__":
    main()
