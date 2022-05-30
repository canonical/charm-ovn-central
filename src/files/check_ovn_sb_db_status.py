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
"""Check OVN DB connections status and alert."""

import os

from nagios_plugin3 import (
    CriticalError,
    UnknownError,
    WarnError,
    check_file_freshness,
    try_check,
)

OUTPUT_FILE = "/var/lib/nagios/ovn_sb_db_status.out"

NAGIOS_ERRORS = {
    "CRITICAL": CriticalError,
    "UNKNOWN": UnknownError,
    "WARNING": WarnError,
}


def parse_output():
    """Read OVN DB status saved in the file and alert."""
    if not os.path.exists(OUTPUT_FILE):
        raise UnknownError(
            "UNKNOWN: {} does not exist".format(OUTPUT_FILE)
        )

    # Check if file is newer than 10min
    try_check(check_file_freshness, OUTPUT_FILE)

    try:
        with open(OUTPUT_FILE, "r") as output_file:
            output = output_file.read()
    except PermissionError as error:
        raise UnknownError(error)

    for startline in NAGIOS_ERRORS:
        if output.startswith("{}: ".format(startline)):
            func = NAGIOS_ERRORS[startline]
            raise func(output)

    print(output)


if __name__ == "__main__":
    try_check(parse_output)
