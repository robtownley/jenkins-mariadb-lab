#!/usr/bin/env python3
"""Start or stop every lab server recorded in Terraform state.

  lab_power.py start   start stopped servers and wait until they run; writes ./instances-started if any started
  lab_power.py stop    stop running servers and wait until they stop; writes ./stop-report.txt

Stopping keeps every disk. A started server gets a new public IP, which APPLY's plan then puts into DNS.
"""
import json
import subprocess
import sys
from pathlib import Path


def lab_instances():
    out = subprocess.run(['terraform', '-chdir=terraform', 'output', '-json', 'nodes'], text=True, capture_output=True)
    if out.returncode != 0:
        return {}  # Nothing deployed yet.
    return {name: node['instance_id'] for name, node in json.loads(out.stdout).items()}


def states(ids):
    if not ids:
        return {}
    out = subprocess.run(['aws', 'ec2', 'describe-instances', '--instance-ids', *ids, '--query',
                          'Reservations[].Instances[].[InstanceId,State.Name]', '--output', 'json'],
                         text=True, capture_output=True, check=True)
    return dict(json.loads(out.stdout))


def select(instances, current, wanted):
    """Names whose instance is in one of the wanted states."""
    return sorted(name for name, instance in instances.items() if current.get(instance) in wanted)


def change(action, instances, names):
    ids = [instances[name] for name in names]
    subprocess.run(['aws', 'ec2', f'{action}-instances', '--instance-ids', *ids, '--output', 'text'],
                   check=True, stdout=subprocess.DEVNULL)
    waiter = 'instance-running' if action == 'start' else 'instance-stopped'
    subprocess.run(['aws', 'ec2', 'wait', waiter, '--instance-ids', *ids], check=True)


def main():
    action = sys.argv[1] if len(sys.argv) == 2 else ''
    if action not in ('start', 'stop'):
        raise SystemExit('Usage: lab_power.py start|stop')
    instances = lab_instances()
    if not instances:
        print('No lab servers in Terraform state.')
        return
    current = states(list(instances.values()))
    if action == 'start':
        # A server still stopping must finish first, or start-instances fails.
        stopping = select(instances, current, {'stopping'})
        if stopping:
            subprocess.run(['aws', 'ec2', 'wait', 'instance-stopped', '--instance-ids',
                            *(instances[n] for n in stopping)], check=True)
            current = states(list(instances.values()))
        names = select(instances, current, {'stopped'})
        if names:
            print('Starting: ' + ', '.join(names), flush=True)
            change('start', instances, names)
            Path('instances-started').write_text('\n'.join(names) + '\n')
        else:
            print('All lab servers are already running.')
    else:
        names = select(instances, current, {'pending', 'running'})
        if names:
            print('Stopping: ' + ', '.join(names), flush=True)
            change('stop', instances, names)
        report = (f'Stopped {len(names)} lab server(s): {", ".join(names) or "none were running"}.\n'
                  'Disks are kept; storage is still billed while servers are stopped.\n')
        Path('stop-report.txt').write_text(report)
        print(report, end='')


if __name__ == '__main__':
    main()
