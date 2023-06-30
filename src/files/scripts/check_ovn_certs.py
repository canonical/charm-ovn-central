import os
import json
from cryptography.hazmat.backends import default_backend
from cryptography import x509
from datetime import datetime

NAGIOS_PLUGIN_DATA = '/usr/local/lib/nagios/juju_charm_plugin_data'


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


def check_ovn_certs(self):
    output_path = os.path.join(NAGIOS_PLUGIN_DATA, 'ovn_cert_status.json')
    for cert in ['/etc/ovn/cert_host', '/etc/ovn/ovn-central.crt']:
        if not os.path.exists(cert):
            message = "cert '{}' does not exist.".format(cert)
            exit_code = 2
            break

        if not os.access(cert, os.R_OK):
            message = "cert '{}' is not readable.".format(cert)
            exit_code = 2
            break

        remaining_days = SSLCertificate(cert).days_remaining
        if remaining_days <= 0:
            message = "{}: cert has expired.".format(cert)
            exit_code = 2
            break

        if remaining_days < 10:
            message = ("{}: cert will expire soon (less than 10 days).".
                       format(cert))
            exit_code = 1
            break
    else:
        message = "all certs healthy"
        exit_code = 0

    ts = datetime.now()
    with open(output_path, 'w') as fd:
        fd.write(json.dumps({'message': message,
                             'exit_code': exit_code,
                             'last_updated':
                             "{}-{}-{} {}:{}:{}".format(ts.year, ts.month,
                                                        ts.day, ts.hour,
                                                        ts.minute,
                                                        ts.second)}))

    os.chmod(output_path, '644')


if __name__ == "__main__":
    check_ovn_certs()
