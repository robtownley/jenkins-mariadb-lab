"""Offline checks for the optional lab features. Run: python3 -m unittest discover -s tests."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def change(address, kind, actions):
    return {'address': address, 'type': kind, 'change': {'actions': actions}}


class PlanReportTests(unittest.TestCase):
    report = module('report_plan', 'scripts/report_plan.py')

    def plan(self, changes, resources=()):
        return {'resource_changes': changes, 'planned_values': {'root_module': {'resources': list(resources)}}}

    def test_removals_cover_deleted_and_replaced_servers_and_disks(self):
        plan = self.plan([
            change('aws_instance.node["heron3"]', 'aws_instance', ['delete']),
            change('aws_ebs_volume.mysql_zfs["heron3-a"]', 'aws_ebs_volume', ['delete', 'create']),
            change('aws_route53_record.lab["heron3"]', 'aws_route53_record', ['delete']),
            change('aws_instance.node["primary"]', 'aws_instance', ['update']),
        ])
        self.assertEqual(self.report.removals(plan), ['aws_instance.node["heron3"]', 'aws_ebs_volume.mysql_zfs["heron3-a"]'])
        summary, _ = self.report.summarise(plan, 'APPLY')
        self.assertIn('ALLOW_REMOVALS', summary)
        summary, _ = self.report.summarise(plan, 'DESTROY')
        self.assertNotIn('ALLOW_REMOVALS', summary)

    def test_monthly_cost_counts_instances_root_disks_and_volumes(self):
        plan = self.plan([], [
            {'type': 'aws_instance', 'values': {'instance_type': 't3.small', 'root_block_device': [{'volume_size': 20}]}},
            {'type': 'aws_ebs_volume', 'values': {'size': 15}},
            {'type': 'aws_instance', 'values': {'instance_type': 'x9.huge', 'root_block_device': []}},
        ])
        compute, storage, unknown = self.report.monthly_cost(plan)
        self.assertAlmostEqual(compute, 0.0236 * 730)
        self.assertAlmostEqual(storage, 35 * 0.0928)
        self.assertEqual(unknown, ['x9.huge'])


class WarehouseSeedTests(unittest.TestCase):
    seed = module('seed_warehouse', 'ansible/files/seed_warehouse.py')

    def test_reads_the_dump_position(self):
        dump = "-- MariaDB dump\n-- SET GLOBAL gtid_slave_pos='0-1-1234';\n-- CHANGE MASTER TO MASTER_USE_GTID=slave_pos;\n"
        self.assertEqual(self.seed.dump_position(dump), '0-1-1234')
        with self.assertRaises(ValueError):
            self.seed.dump_position('no position here')

    def test_merges_independent_domains_and_rejects_overlap(self):
        self.assertEqual(self.seed.merge_positions(['0-1-1234', '2-4001-88']), '0-1-1234,2-4001-88')
        self.assertEqual(self.seed.merge_positions(['2-4001-88', '0-1-5,']), '0-1-5,2-4001-88')
        with self.assertRaises(ValueError):
            self.seed.merge_positions(['0-1-1234', '0-4001-9'])
        with self.assertRaises(ValueError):
            self.seed.merge_positions(['not-a-gtid'])

    def test_quotes_sql_strings(self):
        self.assertEqual(self.seed.quote("it's"), "'it\\'s'")


class LagSampleTests(unittest.TestCase):
    sampler = module('lab_lag_sample', 'ansible/files/lab_lag_sample.py')

    def test_tracks_max_lag_and_broken_samples_per_channel(self):
        ok = {'Connection_name': '', 'Slave_IO_Running': 'Yes', 'Slave_SQL_Running': 'Yes', 'Seconds_Behind_Master': '7'}
        stopped = {**ok, 'Slave_SQL_Running': 'No', 'Seconds_Behind_Master': 'NULL'}
        caught_up = {**ok, 'Seconds_Behind_Master': '0'}
        result = self.sampler.summarise([[ok], None, [stopped], [caught_up]])
        self.assertEqual(result['samples'], 4)
        self.assertEqual(result['unreachable_samples'], 1)
        self.assertEqual(result['channels']['default'], {'max_lag': 7, 'broken_samples': 1, 'last_lag': 0})


class RetentionTests(unittest.TestCase):
    def test_backup_names_and_pruning(self):
        backup = module('lab_backup', 'scripts/lab_backup.py')
        self.assertTrue(backup.KEY.match('20261113T230000Z-replica2.xbstream.gz'))
        self.assertFalse(backup.KEY.match('notes.txt'))
        names = [f'2026111{d}T000000Z-replica2.xbstream.gz' for d in range(1, 6)]
        self.assertEqual(backup.to_prune(names, 3), names[:2])
        self.assertEqual(backup.to_prune(names, 7), [])

    def test_image_pruning_keeps_the_newest(self):
        images = module('lab_images', 'scripts/lab_images.py')
        newest_first = ['c', 'b', 'a']
        self.assertEqual(images.to_delete(newest_first, 2), ['a'])
        self.assertEqual(images.to_delete(newest_first, 0), [])

    def test_power_selects_by_state(self):
        power = module('lab_power', 'scripts/lab_power.py')
        instances = {'primary': 'i-1', 'replica1': 'i-2', 'ssm': 'i-3'}
        states = {'i-1': 'stopped', 'i-2': 'running', 'i-3': 'stopped'}
        self.assertEqual(power.select(instances, states, {'stopped'}), ['primary', 'ssm'])


class FeatureInventoryTests(unittest.TestCase):
    def build(self, extra):
        names = ['primary', 'replica1', 'sdds1', 'affiliate-feed1', 'heron1', 'maxscale', 'monitor', 'ssm', *extra]
        nodes = {name: {'private_ip': f'172.31.2.{i + 1}', 'public_ip': f'203.0.113.{i + 1}', 'zfs_volume_ids': []}
                 for i, name in enumerate(names)}
        with tempfile.TemporaryDirectory() as work:
            Path(work, 'nodes.json').write_text(json.dumps(nodes))
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/make_inventory.py')], cwd=work,
                                    text=True, capture_output=True)
            inventory = json.loads(Path(work, 'inventory.json').read_text()) if result.returncode == 0 else None
        return nodes, result, inventory

    def test_multisource_and_loadgen_groups(self):
        _, result, inv = self.build(['orders-primary', 'warehouse', 'loadgen1', 'loadgen2'])
        self.assertEqual(result.returncode, 0, result.stderr)
        groups = inv['all']['children']
        multisource = groups['multisource']['children']
        self.assertEqual(list(multisource['orders_writer']['hosts']), ['orders-primary'])
        self.assertEqual(list(multisource['warehouse_nodes']['hosts']), ['warehouse'])
        self.assertEqual(list(groups['loadgen']['hosts']), ['loadgen1', 'loadgen2'])
        orders = multisource['orders_writer']['hosts']['orders-primary']
        warehouse = multisource['warehouse_nodes']['hosts']['warehouse']
        self.assertEqual((orders['mariadb_server_id'], orders['gtid_domain_id']), (4001, 2))
        self.assertEqual((warehouse['mariadb_server_id'], warehouse['gtid_domain_id']), (5001, 5))
        # New nodes stay out of the replica topology, rollout order and MaxScale.
        self.assertNotIn('warehouse', groups['rollout_order']['hosts'])
        self.assertNotIn('loadgen1', groups['database']['children']['replicas']['children']['intermediate_replicas']['hosts'])

    def test_feature_groups_exist_when_features_are_off(self):
        _, result, inv = self.build([])
        self.assertEqual(result.returncode, 0, result.stderr)
        groups = inv['all']['children']
        self.assertEqual(groups['loadgen']['hosts'], {})
        self.assertEqual(groups['multisource']['children']['warehouse_nodes']['hosts'], {})

    def test_multisource_nodes_come_as_a_pair(self):
        _, result, _ = self.build(['warehouse'])
        self.assertNotEqual(result.returncode, 0)

    def test_hosts_helper_accepts_feature_roles(self):
        helper = module('hosts_helper_features', 'scripts/update_jenkins_hosts.py')
        nodes, _, _ = self.build([])
        nodes.update({'orders-primary': {'private_ip': '172.31.3.1'}, 'warehouse': {'private_ip': '172.31.3.2'},
                      'loadgen1': {'private_ip': '172.31.3.3'}})
        aliases = {role: alias for _, _, role, alias in helper.entries(nodes)}
        self.assertEqual(aliases['orders-primary'], 'mariadb-orders-primary')
        self.assertEqual(aliases['loadgen1'], 'mariadb-loadgen-1')
        with self.assertRaises(ValueError):
            helper.entries({'loadgen12': {'private_ip': '172.31.3.4'}})


if __name__ == '__main__':
    unittest.main()
