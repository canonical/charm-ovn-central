#!/usr/bin/env python3
# Copyright (C) 2023 Canonical
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

import json
import os
import sys
from datetime import datetime, timedelta

NAGIOS_PLUGIN_DATA = '/usr/local/lib/nagios/juju_charm_plugin_data'


if __name__ == "__main__":
    output_path = os.path.join(NAGIOS_PLUGIN_DATA, 'ovn_cert_status.json')
    if os.path.exists(output_path):
        with open(output_path) as fd:
            try:
                status = json.loads(fd.read())
                ts = datetime.strptime(status['last_updated'],
                                       "%Y-%m-%d %H:%M:%S")
                if datetime.now() - ts > timedelta(days=1):
                    print("ovn cert check status is more than 24 hours old "
                          "(last_updated={})".format(status['last_updated']))
                    sys.exit(1)

                print(status['message'])
                sys.exit(status['exit_code'])
            except ValueError:
                print("invalid check output")
    else:
        print("no info available")

    sys.exit(0)
