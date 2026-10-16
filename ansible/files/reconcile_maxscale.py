#!/usr/bin/python3
"""Reconcile only this lab's direct-tier backends through MaxCtrl, never restart it."""
import argparse
import json
import re
import subprocess
import time
from pathlib import Path


def command(*args):
    return subprocess.check_output(['maxctrl', *args], text=True)


def get(path):
    return json.loads(command('api', 'get', path))['data']


def members(kind, name):
    return {row['id'] for row in get(f'{kind}/{name}').get('relationships', {}).get('servers', {}).get('data', [])}


def reconcile(phase):
    desired = json.loads(Path('/etc/maxscale-lab-members.json').read_text())
    if 'primary' not in desired or any(not re.fullmatch(r'primary|replica[1-9][0-9]*', n) for n in desired):
        raise RuntimeError('Invalid direct-tier membership')
    changed = False
    service = 'ReadWrite-Service'
    monitor = 'MariaDB-Monitor'
    existing = {row['id']: row for row in get('servers')}
    routed = members('services', service)
    monitored = members('monitors', monitor)
    # Only objects with our fixed lab naming convention are owned by this helper.
    retired = {name for name in existing if re.fullmatch(r'replica[1-9][0-9]*', name)} - desired.keys()
    for name in sorted(retired):
        command('set', 'server', name, 'drain')
        for _ in range(60):
            if int(get(f'servers/{name}')['attributes']['statistics']['connections']) == 0:
                break
            time.sleep(2)
        else:
            raise RuntimeError(f'{name} still has connections; refusing removal')
        if name in routed:
            command('unlink', 'service', service, name)
        if name in monitored:
            command('unlink', 'monitor', monitor, name)
        command('destroy', 'server', name)
        changed = True
    for name, ip in desired.items():
        if name not in existing:
            command('create', 'server', name, ip, '3306')
            changed = True
        elif existing[name]['attributes']['parameters']['address'] != ip:
            # A replaced EC2 backend can retain its stable MaxScale object name.
            command('set', 'server', name, 'drain')
            for _ in range(60):
                if int(get(f'servers/{name}')['attributes']['statistics']['connections']) == 0:
                    break
                time.sleep(2)
            else:
                raise RuntimeError(f'{name} still has old connections; refusing address change')
            # Do not clear isolation applied by an administrator.
            old_state = existing[name]['attributes'].get('state', '').lower()
            command('alter', 'server', name, 'address', ip)
            if 'drain' not in old_state:
                command('clear', 'server', name, 'drain')
            changed = True
        if name not in monitored:
            command('link', 'monitor', monitor, name)
            changed = True
    # A first deployment starts with primary-only routing. Replicas join only
    # after the database play has checked threads, upstream and zero lag.
    if phase == 'route':
        routed = members('services', service)
        for name in desired:
            if name in routed:
                continue
            for _ in range(30):
                state = get(f'servers/{name}')['attributes'].get('state', '').lower()
                states = {s.strip() for s in state.split(',')}
                if states & {'maintenance', 'draining', 'drained', 'drain'}:
                    raise RuntimeError(f'{name} is administratively isolated; leaving it isolated')
                if 'running' in states and states & {'master', 'primary', 'slave', 'replica'}:
                    break
                time.sleep(2)
            else:
                raise RuntimeError(f'{name} is not ready for routing: {state}')
            command('link', 'service', service, name)
            changed = True
    print('CHANGED' if changed else 'UNCHANGED')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=['monitor', 'route'], required=True)
    reconcile(parser.parse_args().phase)
