import os
import sys
from datetime import datetime

from cryptography.hazmat.backends import default_backend
from cryptography import x509

EXIT_OK = 0
EXIT_CRIT = 2


class SSLCertificate(object):

    def __init__(self, path):
        self.path = path

    @property
    def cert(self):
        with open(self.path, "rb") as fd:
            self.certificate = fd.read()

    @property
    def expiry_date(self):
        cert = x509.load_pem_x509_certificate(self.cert, default_backend())
        return cert.not_valid_after

    @property
    def days_remaining(self):
        return int((self.expiry_date - datetime.now()).days)


if __name__ == "__main__":
    for cert in ['/etc/ovn/cert_host', '/etc/ovn/ovn-chassis.crt']:
        if not os.path.exists(cert):
            print("cert '{}' does not exist.".format(cert))
            sys.exit(EXIT_CRIT)

        if os.access(cert, os.R_OK) == 0:
            print("cert '{}' is not readable.".format(cert))
            sys.exit(EXIT_CRIT)

        if SSLCertificate(cert).days_remaining <= 0:
            print("{}: cert has expired.".format(cert))
            sys.exit(EXIT_CRIT)

        sys.exit(EXIT_OK)
