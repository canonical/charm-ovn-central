# Overview

This charm provides the Northbound and Southbound OVSDB Databases and the
Open Virtual Network (OVN) central control daemon (`ovn-northd`).

> **Note**: The OVN charms are considered preview charms.

# Usage

OVN makes use of Public Key Infrastructure (PKI) to authenticate and authorize
control plane communication.  The charm requires a Certificate Authority to be
present in the model as represented by the `certificates` relation.

There is a [OVN overlay bundle](https://github.com/openstack-charmers/openstack-bundles/blob/master/development/overlays/openstack-base-ovn.yaml)
for use in conjunction with the [OpenStack Base bundle](https://github.com/openstack-charmers/openstack-bundles/blob/master/development/openstack-base-bionic-train/bundle.yaml).

It gives an example of how you can realize this with the help from
[Vault](https://jaas.ai/vault/).

## Network Spaces support

This charm supports the use of Juju Network Spaces.

By binding the `ovsdb`, `ovsdb-cms` and `ovsdb-peer` endpoints you can
influence which interface will be used for communication with consumers of
the Southbound DB, Cloud Management Systems (CMS) and cluster internal
communication.

    juju deploy ovn-central --bind "''=oam-space ovsdb=data-space"

# Bugs

Please report bugs on [Launchpad](https://bugs.launchpad.net/charm-ovn-central/+filebug).

For general questions please refer to the OpenStack [Charm Guide](https://docs.openstack.org/charm-guide/latest/).
