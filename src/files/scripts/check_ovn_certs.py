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

import os
import json
from datetime import datetime

from cryptography.hazmat.backends import default_backend
from cryptography import x509

NAGIOS_PLUGIN_DATA = '/usr/local/lib/nagios/juju_charm_plugin_data'
UNKNOWN = 3
CRITICAL = 2
WARN = 1
SUCCESS = 0

CERT_EXPIRY_CRITICAL_LIMIT = 30
CERT_EXPIRY_WARN_LIMIT = 60


class SSLCertificate(object):
    def __init__(self, path):
        self.path = path

    @property
    def cert(self):
        with open(self.path, "rb") as fd:
            return fd.read()

    @property
    def expiry_date(self):
        cert = x509.load_pem_x509_certificate(self.cert, default_backend())
        return cert.not_valid_after

    @property
    def days_remaining(self):
        return int((self.expiry_date - datetime.now()).days)


def check_ovn_certs():
    output_path = os.path.join(NAGIOS_PLUGIN_DATA, 'ovn_cert_status.json')
    if not os.path.isdir(NAGIOS_PLUGIN_DATA):
        os.makedirs(NAGIOS_PLUGIN_DATA)

    exit_code = SUCCESS
    for cert in ['/etc/ovn/cert_host', '/etc/ovn/ovn-central.crt']:
        if not os.path.exists(cert):
            message = "cert '{}' does not exist.".format(cert)
            exit_code = CRITICAL
            break

        if not os.access(cert, os.R_OK):
            message = "cert '{}' is not readable.".format(cert)
            exit_code = CRITICAL
            break

        try:
            remaining_days = SSLCertificate(cert).days_remaining
            if remaining_days <= 0:
                message = "{}: cert has expired.".format(cert)
                exit_code = CRITICAL
                break

            if remaining_days < CERT_EXPIRY_CRITICAL_LIMIT:
                message = ("{}: cert will expire in {} days".
                           format(cert, remaining_days))
                exit_code = CRITICAL
                break

            if remaining_days < CERT_EXPIRY_WARN_LIMIT:
                message = ("{}: cert will expire in {} days".
                           format(cert, remaining_days))
                exit_code = WARN
                break

        except Exception as exc:
            message = "failed to check cert '{}': {}".format(cert, str(exc))
            exit_code = UNKNOWN
    else:
        message = "all certs healthy"
        exit_code = SUCCESS

    ts = datetime.now()
    with open(output_path, 'w') as fd:
        fd.write(json.dumps({'message': message,
                             'exit_code': exit_code,
                             'last_updated':
                             "{}-{}-{} {}:{}:{}".format(ts.year, ts.month,
                                                        ts.day, ts.hour,
                                                        ts.minute,
                                                        ts.second)}))

    os.chmod(output_path, 644)


if __name__ == "__main__":
    check_ovn_certs()
