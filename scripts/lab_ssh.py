"""SSH to a lab node the same way Ansible does (same key, known-hosts file and public address)."""
import json
import shlex
from pathlib import Path


def ssh_command(node, remote_command):
    inventory = json.loads(Path('inventory.json').read_text())
    settings = inventory['all']['vars']
    nodes = json.loads(Path('nodes.json').read_text())
    if node not in nodes:
        raise SystemExit(f'{node} is not a lab node')
    return ['ssh', '-i', settings['ansible_ssh_private_key_file'], '-o', 'BatchMode=yes',
            *shlex.split(settings['ansible_ssh_common_args']),
            f"{settings['ansible_user']}@{nodes[node]['public_ip']}", remote_command]


def last_intermediate_replica():
    """The highest-numbered intermediate replica: never the primary, and the last to be removed."""
    nodes = json.loads(Path('nodes.json').read_text())
    replicas = sorted((name for name in nodes if name.startswith('replica') and name[7:].isdigit()),
                      key=lambda name: int(name[7:]))
    if not replicas:
        raise SystemExit('No intermediate replica to use')
    return replicas[-1]
