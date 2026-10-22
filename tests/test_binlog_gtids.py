import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('gtid_check', Path(__file__).resolve().parents[1] / 'scripts/check_binlog_gtids.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)

class Cursor:
    def __init__(self, rows):
        self.rows = rows
    def execute(self, sql, args=None):
        self.result = [{'Log_name': 'mariadb-bin.000001'}] if args is None else [r for r in self.rows if r['Pos'] >= args[1]][:args[2]]
    def fetchall(self):
        return self.result

def event(position, kind, info):
    return {'Pos': position, 'End_log_pos': position+100, 'Event_type': kind, 'Info': info}

class GTIDTests(unittest.TestCase):
    def test_exact_event_is_required(self):
        cursor = Cursor([event(4, 'Gtid', 'GTID 0-1-28 trans'), event(104, 'Gtid', 'GTID 0-1-30 trans')])
        with self.assertRaises(RuntimeError):
            checker.check(cursor, '0-1-29')
    def test_state_header_does_not_prove_an_event_exists(self):
        cursor = Cursor([event(4, 'Gtid_list', '[0-1-29]')])
        with self.assertRaises(RuntimeError):
            checker.check(cursor, '0-1-29')
    def test_post_catchup_event_passes(self):
        cursor = Cursor([event(4, 'Gtid', 'BEGIN GTID 0-1-30')])
        self.assertEqual(checker.check(cursor, '0-1-30'), 1)
    def test_scan_is_bounded(self):
        cursor = Cursor([event(4, 'Gtid', 'GTID 0-1-28 trans'), event(104, 'Gtid', 'GTID 0-1-29 trans')])
        with self.assertRaises(RuntimeError):
            checker.check(cursor, '0-1-29', max_events=1)
    def test_bad_input_rejected(self):
        with self.assertRaises(ValueError):
            checker.parse_gtids('0-1-29; arbitrary')

if __name__ == '__main__':
    unittest.main()
