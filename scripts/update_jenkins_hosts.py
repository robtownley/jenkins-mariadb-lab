#!/usr/bin/python3
"""Root-owned, fixed-target hosts updater. Accepts only lab node JSON on stdin."""
import fcntl
import ipaddress
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

HOSTS = Path('/etc/hosts')
LOCK = Path('/run/lock/mariadb-lab-hosts.lock')
BEGIN = '# BEGIN JENKINS MARIADB LAB'
END = '# END JENKINS MARIADB LAB'
MAX_INPUT = 65536


def entries(nodes):
    if not isinstance(nodes, dict) or len(nodes) > 101:
        raise ValueError('Expected a bounded lab nodes object')
    result = []
    for role, node in nodes.items():
        if role == 'primary':
            alias, order = 'mariadb-primary', (0, 0)
        elif role == 'ssm':
            alias, order = 'mariadb-ssm', (4, 0)
        elif role == 'monitor':
            alias, order = 'mariadb-monitor', (3, 0)
        elif role == 'maxscale':
            alias, order = 'mariadb-maxscale', (2, 0)
        elif re.fullmatch(r'replica[1-9][0-9]?', role):
            number = int(role[7:])
            alias, order = f'mariadb-replica-{number}', (1, number)
        else:
            raise ValueError(f'Unexpected lab role: {role!r}')
        if not isinstance(node, dict):
            raise ValueError('Invalid node record')
        address = ipaddress.ip_address(node['private_ip'])
        if not isinstance(address, ipaddress.IPv4Address) or not address.is_private or address.is_loopback or address.is_unspecified or address.is_link_local or address.is_multicast:
            raise ValueError(f'Expected a private EC2 IPv4 address for {role}')
        result.append((order, str(address), role, alias))
    return sorted(result)


def render(existing, nodes):
    rows = entries(nodes)
    lines = existing.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.strip() == BEGIN]
    ends = [i for i, line in enumerate(lines) if line.strip() == END]
    if len(starts) != len(ends) or len(starts) > 1 or (starts and starts[0] >= ends[0]):
        raise ValueError('Malformed managed hosts block; refusing to edit')
    if starts:
        before, after = lines[:starts[0]], lines[ends[0] + 1:]
    else:
        before, after = lines, []
    aliases = {value for _, _, role, alias in rows for value in (role, alias)}
    for line in before + after:
        fields = line.split('#', 1)[0].split()
        if aliases.intersection(fields[1:]):
            raise ValueError('Lab hostname already exists outside the managed block; inspect /etc/hosts')
    block = ''
    if rows:
        block = BEGIN + '\n' + ''.join(f'{ip}\t{role} {alias}\n' for _, ip, role, alias in rows) + END + '\n'
        if before and not before[-1].endswith('\n'):
            before[-1] += '\n'
    return ''.join(before) + block + ''.join(after)


def main():
    if len(sys.argv) != 1 or os.geteuid() != 0:
        raise ValueError('Run the installed helper as root with no arguments')
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        raise ValueError('Input is too large')
    nodes = json.loads(raw)
    # Validate before taking the file lock or touching /etc/hosts.
    entries(nodes)
    with LOCK.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if HOSTS.is_symlink() or not HOSTS.is_file():
            raise ValueError('/etc/hosts must be a regular file')
        existing = HOSTS.read_text()
        updated = render(existing, nodes)
        if updated == existing:
            print('Jenkins hosts entries already current')
            return
        backup = HOSTS.with_name('hosts.before-mariadb-lab')
        if not backup.exists():
            shutil.copy2(HOSTS, backup)
            os.chown(backup, 0, 0)
            backup.chmod(0o600)
        descriptor, temporary = tempfile.mkstemp(prefix='.hosts-mariadb-', dir=HOSTS.parent)
        try:
            metadata = HOSTS.stat()
            with os.fdopen(descriptor, 'w') as output:
                output.write(updated)
                output.flush()
                os.fsync(output.fileno())
            shutil.copystat(HOSTS, temporary)
            os.chown(temporary, metadata.st_uid, metadata.st_gid)
            os.replace(temporary, HOSTS)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    print('Jenkins /etc/hosts lab entries updated: ' + ', '.join(role for _, _, role, _ in entries(nodes)))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as error:
        print(f'Hosts update failed: {error}', file=sys.stderr)
        sys.exit(1)
