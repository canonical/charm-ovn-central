# TODO: much of this code is shared with the ``ovn-*-chassis`` charms and we
# should move this to a layer or library.
import json
import os
import subprocess

import charmhelpers.core as ch_core


def _run(*args):
    """Run a process, check result, capture decoded output from STDERR/STDOUT.

    :param args: Command and arguments to run
    :type args: Union
    :returns: Information about the completed process
    :rtype: subprocess.CompletedProcess
    :raises subprocess.CalledProcessError
    """
    return subprocess.run(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True,
        universal_newlines=True)


def cluster_status(target, schema):
    cp = _run('ovs-appctl', '-t', target, 'cluster/status', schema)
    r = {}
    key = ''
    for line in cp.stdout.splitlines():
        if line.startswith(' ') and key:
            if key not in r:
                r[key] = []
            r[key].append(line.strip())
        elif ':' in line:
            key, value = line.split(':', 1)
            if not value:
                continue
            key = key.lower().replace(' ', '_')
            if key in ('cluster_id', 'server_id'):
                value = value.split()
                value[1] = value[1][1:-1]
            else:
                value = value.strip()
            r[key] = value
        else:
            continue
    return r


def is_cluster_leader(target, schema):
    if not os.path.exists(target):
        return False
    cs = cluster_status(target, schema)
    role = cs.get('role')
    return role == 'leader'


def del_chassis(chassis):
    ch_core.hookenv.log('del_chassis({})'.format(chassis),
                        level=ch_core.hookenv.INFO)


def add_chassis(chassis, encap_type, encap_ip):
    ch_core.hookenv.log('add_chassis({})'.format((chassis,
                                                  encap_type,
                                                  encap_ip)),
                        level=ch_core.hookenv.INFO)


class SimpleOVSDB(object):
    """Simple interface to OVSDB through the use of command line tools.

    OVS and OVN is managed through a set of databases.  These databases have
    similar command line tools to manage them.  We make use of the similarity
    to provide a generic class that can be used to manage them.

    The OpenvSwitch project does provide a Python API, but on the surface it
    appears to be a bit too involved for our simple use case.

    Examples:
    chassis = SimpleOVSDB('ovn-sbctl', 'chassis')
    for chs in chassis:
        print(chs)

    bridges = SimpleOVSDB('ovs-vsctl', 'bridge')
    for br in bridges:
        if br['name'] == 'br-test':
            bridges.set(br['uuid'], 'external_ids:charm', 'managed')
    """

    def __init__(self, tool, table):
        """SimpleOVSDB constructor

        :param tool: Which tool with database commands to operate on.
                     Usually one of `ovs-vsctl`, `ovn-nbctl`, `ovn-sbctl`
        :type tool: str
        :param table: Which table to operate on
        :type table: str
        """
        self.tool = tool
        self.tbl = table

    def _get(self, record, key):
        cp = _run(self.tool, '-f', 'json', 'get', self.tbl, record, key)
        ch_core.hookenv.log(cp, level=ch_core.hookenv.INFO)
        return json.loads(cp.stdout)

    def __getitem__(self, rec_key):
        try:
            return self._get(*rec_key)
        except subprocess.CalledProcessError:
            raise KeyError

    def _find_tbl(self, condition=None):
        cmd = [self.tool, '-f', 'json', 'find', self.tbl]
        if condition:
            cmd.append(condition)
        cp = _run(*cmd)
        data = json.loads(cp.stdout)
        for row in data['data']:
            values = []
            for col in row:
                if isinstance(col, list):
                    values.append(col[1])
                else:
                    values.append(col)
            yield dict(zip(data['headings'], values))

    def __iter__(self):
        return self._find_tbl()

    def clear(self, rec, col):
        _run(self.tool, 'clear', self.tbl, rec, col)

    def find(self, condition):
        return self._find_tbl(condition=condition)

    def remove(self, rec, col, value):
        _run(self.tool, 'remove', self.tbl, rec, col, value)

    def set(self, rec, col, value):
        _run(self.tool, 'set', self.tbl, rec, '{}={}'.format(col, value))
