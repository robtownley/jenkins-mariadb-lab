"""Offline topology/inventory contracts. Run: python3 -m unittest discover -s tests."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


class TopologyTests(unittest.TestCase):
    def inventory(self, count, omit=None):
        names = ['primary'] + [f'replica{n}' for n in range(1, count + 1)]
        names += [f'{family}{n}' for n in range(1, count + 1) for family in ('sdds', 'affiliate-feed', 'heron')]
        names += ['maxscale', 'monitor', 'ssm']
        nodes = {name: {'private_ip': f'172.31.1.{i+1}', 'public_ip': f'203.0.113.{i+1}',
                        'zfs_volume_ids': []} for i, name in enumerate(names) if name != omit}
        with tempfile.TemporaryDirectory() as work:
            Path(work, 'nodes.json').write_text(json.dumps(nodes))
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/make_inventory.py')], cwd=work,
                                    text=True, capture_output=True)
            if omit:
                self.assertNotEqual(result.returncode, 0)
                return
            self.assertEqual(result.returncode, 0, result.stderr)
            inv = json.loads(Path(work, 'inventory.json').read_text())
            order = Path(work, 'database-order.txt').read_text().splitlines()
        return nodes, inv, order

    def test_supported_counts_stable_ids_and_parents(self):
        host_helper = module('hosts_helper', 'scripts/update_jenkins_hosts.py')
        for count in (1, 2, 3, 6):
            nodes, inv, order = self.inventory(count)
            db = inv['all']['children']['database']['children']
            tiers = db['replicas']['children']
            direct = tiers['intermediate_replicas']['hosts']
            children = tiers['downstream_replicas']['hosts']
            self.assertEqual(len(direct), count)
            self.assertEqual(len(children), count * 3)
            self.assertEqual(len(order), 1 + count * 4)
            self.assertEqual(order[-1], 'primary')
            ids = [1] + [h['mariadb_server_id'] for h in list(direct.values()) + list(children.values())]
            self.assertEqual(len(ids), len(set(ids)))
            for n in range(1, count + 1):
                self.assertEqual(direct[f'replica{n}']['mariadb_server_id'], n + 1)
                for family in ('sdds', 'affiliate-feed', 'heron'):
                    self.assertEqual(children[f'{family}{n}']['replication_upstream'], f'replica{n}')
            rendered = host_helper.render('127.0.0.1 localhost\n', nodes)
            self.assertIn('affiliate-feed1', rendered)
            self.assertEqual(rendered, host_helper.render(rendered, nodes))

    def test_home_ssh_keeps_replication_addresses_private(self):
        nodes, inv, _ = self.inventory(2)
        def visit(group):
            for name, host in group.get('hosts', {}).items():
                self.assertEqual(host['ansible_host'], nodes[name]['private_ip'])
                args = host['ansible_ssh_common_args']
                self.assertIn('-o Hostname=' + nodes[name]['public_ip'], args)
                self.assertIn('IdentitiesOnly=yes', args)
                self.assertIn('UserKnownHostsFile=', args)
            for child in group.get('children', {}).values():
                visit(child)
        visit(inv['all'])

    def test_missing_parent_rejected(self):
        self.inventory(2, omit='replica2')

    def test_maxscale_membership_is_idempotent_and_excludes_children(self):
        helper = module('runtime_helper', 'ansible/files/reconcile_maxscale.py')
        state = {'primary': {'id': 'primary', 'attributes': {'parameters': {'address': '172.31.1.1'},
                         'state': 'Master, Running', 'statistics': {'connections': 0}}}}
        relations = {'services': {'primary'}, 'monitors': {'primary'}}
        commands = []
        def get(path):
            if path == 'servers':
                return list(state.values())
            kind, name = path.split('/')
            if kind == 'servers':
                return state[name]
            return {'relationships': {'servers': {'data': [{'id': n} for n in relations[kind]]}}}
        def command(*args):
            commands.append(args)
            if args[:2] == ('create', 'server'):
                state[args[2]] = {'id': args[2], 'attributes': {'parameters': {'address': args[3]},
                    'state': 'Slave, Running', 'statistics': {'connections': 0}}}
            elif args[0] == 'link':
                relations[{'service': 'services', 'monitor': 'monitors'}[args[1]]].add(args[3])
            return ''
        with tempfile.TemporaryDirectory() as work:
            desired = Path(work, 'members.json')
            desired.write_text(json.dumps({'primary': '172.31.1.1', 'replica1': '172.31.1.2'}))
            with patch.object(helper, 'Path', return_value=desired), patch.object(helper, 'get', side_effect=get), patch.object(helper, 'command', side_effect=command):
                helper.reconcile('monitor')
                self.assertNotIn('replica1', relations['services'])
                helper.reconcile('route')
                self.assertIn('replica1', relations['services'])
                commands.clear()
                helper.reconcile('route')
                self.assertFalse(commands)
        self.assertFalse(any('stop' in c or 'restart' in c for c in commands))


if __name__ == '__main__':
    unittest.main()
