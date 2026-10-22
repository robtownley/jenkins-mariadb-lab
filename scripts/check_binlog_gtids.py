#!/usr/bin/env python3
"""Require actual GTID events in retained binlogs before moving a lab replica."""
import re
import sys


def parse_gtids(value):
    if not re.fullmatch(r'[0-9]+-[0-9]+-[0-9]+(?:,[0-9]+-[0-9]+-[0-9]+)*', value):
        raise ValueError('Invalid GTID list')
    return set(value.split(','))


def check(cursor, requested, max_events=50000):
    missing = parse_gtids(requested)
    cursor.execute('SHOW BINARY LOGS')
    logs = cursor.fetchall()
    scanned = 0
    for log in reversed(logs):
        position = 4
        while scanned < max_events:
            cursor.execute('SHOW BINLOG EVENTS IN %s FROM %s LIMIT %s',
                           (log['Log_name'], position, min(1000, max_events-scanned)))
            rows = cursor.fetchall()
            if not rows:
                break
            scanned += len(rows)
            for row in rows:
                # Gtid_list is a state/header event, not evidence that the
                # corresponding transaction is available in this binlog.
                if row['Event_type'].lower() != 'gtid':
                    continue
                found = re.search(r'\bGTID\s+([0-9]+-[0-9]+-[0-9]+)\b', row.get('Info') or '', re.I)
                if found:
                    missing.discard(found.group(1))
            if not missing:
                return scanned
            next_position = int(rows[-1]['End_log_pos'])
            if next_position <= position:
                raise RuntimeError('Non-advancing binlog event position')
            position = next_position
        if scanned >= max_events:
            break
    raise RuntimeError('Exact GTID event(s) unavailable within the bounded binlog scan: '
                       + ','.join(sorted(missing)) + '. Channel has not been moved to this intermediate.')


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Usage: check_binlog_gtids.py GTID_LIST')
    parse_gtids(sys.argv[1])
    import pymysql
    with pymysql.connect(user='root', unix_socket='/run/mysqld/mysqld.sock',
                         autocommit=True, cursorclass=pymysql.cursors.DictCursor) as connection:
        with connection.cursor() as cursor:
            scanned = check(cursor, sys.argv[1])
    print(f'Exact GTID events verified: {sys.argv[1]} ({scanned} events inspected)')


if __name__ == '__main__':
    main()
