# Overview

The ovn-central charm provides the Northbound and Southbound OVSDB Databases
and the Open Virtual Network (OVN) central control daemon (`ovn-northd`). It is
used in conjunction with either the [ovn-chassis][ovn-chassis-charm]
subordinate charm or the [ovn-dedicated-chassis][ovn-dedicated-chassis-charm]
principle charm.

> **Note**: The OVN charms are supported starting with OpenStack Train.

# Usage

The [OpenStack Base bundle][openstack-base-bundle] gives an example of how you
can deploy OpenStack and OVN with [Vault][vault-charm] to automate certificate
lifecycle management.

OVN makes use of Public Key Infrastructure (PKI) to authenticate and authorize
control plane communication. The charm therefore requires a Certificate
Authority to be present in the model as represented by the `certificates`
relation.

Refer to [Open Virtual Network (OVN)][cdg-ovn] in the [OpenStack Charms
Deployment Guide][cdg] for details, including deployment steps.

> **Note**: The ovn-central charm requires a minimum of three units to operate.

## Network spaces

This charm supports the use of Juju network spaces.

By binding the `ovsdb`, `ovsdb-cms` and `ovsdb-peer` endpoints you can
influence which interface will be used for communication with consumers of the
Southbound DB, Cloud Management Systems (CMS) and cluster internal
communication.

    juju deploy -n 3 --series focal \
        --bind "''=oam-space ovsdb=data-space" \
        ovn-central

## OVN RBAC and securing the OVN services

The charm enables [RBAC][ovn-rbac] in the OVN Southbound database by default.
The RBAC feature enforces authorization of individual chassis connecting to the
database, and also restricts database operations.

In the event of an individual chassis being compromised, RBAC will make it more
difficult to leverage database access for compromising other parts of the
network.

> **Note**: Due to how RBAC is implemented in [ovsdb-server][ovsdb-server]
  the charm opens up a separate listener at port 16642 for connections from
  [ovn-northd][ovn-northd].

The charm automatically enables the firewall and will allow traffic from its
cluster peers to port 6641, 6643, 6644 and 16642. CMS clients will be allowed
to talk to port 6641.

Anyone will be allowed to connect to port 6642.

# Bugs

Please report bugs on [Launchpad][lp-ovn-central].

For general questions please refer to the [OpenStack Charm Guide][cg].

<!-- LINKS -->

[cg]: https://docs.openstack.org/charm-guide/latest/
[cdg]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide/latest/
[cdg-ovn]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide/latest/app-ovn.html
[ovn-rbac]: https://github.com/ovn-org/ovn/blob/master/Documentation/topics/role-based-access-control.rst
[ovsdb-server]: https://github.com/openvswitch/ovs/blob/master/Documentation/ref/ovsdb-server.7.rst#413-transact
[ovn-northd]: https://manpages.ubuntu.com/manpages/eoan/en/man8/ovn-northd.8.html
[lp-ovn-central]: https://bugs.launchpad.net/charm-ovn-central/+filebug
[openstack-base-bundle]: https://github.com/openstack-charmers/openstack-bundles/blob/master/development/openstack-base-bionic-ussuri-ovn/bundle.yaml
[vault-charm]: https://jaas.ai/vault
[ovn-chassis-charm]: https://jaas.ai/ovn-chassis
[ovn-dedicated-chassis-charm]: https://jaas.ai/ovn-dedicated-chassis
