#!/usr/bin/python3
"""Seed and connect the multi-source warehouse. Idempotent; prints CHANGED or UNCHANGED.

warehouse follows two independent primaries over named channels:
  main   <- primary         (lab.*,    GTID domain 0)
  orders <- orders-primary  (orders.*, GTID domain 2)
A fresh warehouse is seeded from consistent dumps of both, and starts each channel at its dump's GTID.
"""
import os
import re
import subprocess
import sys
import tempfile

CHANNELS = {
    # channel: (host environment variable, database)
    'main': ('PRIMARY_HOST', 'lab'),
    'orders': ('ORDERS_HOST', 'orders'),
}
GTID_LINE = re.compile(r"^-- SET GLOBAL gtid_slave_pos='([0-9,-]*)';", re.M)
GTID = re.compile(r'^([0-9]+)-([0-9]+)-([0-9]+)$')


def dump_position(dump_text):
    """The GTID position recorded by mariadb-dump --gtid --master-data=2."""
    found = GTID_LINE.findall(dump_text)
    if len(found) != 1:
        raise ValueError('Dump does not contain exactly one gtid_slave_pos line')
    return found[0]


def merge_positions(positions):
    """Combine per-source GTID positions into one gtid_slave_pos; sources must not share a domain."""
    domains = {}
    for position in positions:
        for gtid in filter(None, position.split(',')):
            match = GTID.match(gtid.strip())
            if not match:
                raise ValueError(f'Invalid GTID {gtid!r}')
            domain = int(match.group(1))
            if domain in domains and domains[domain] != gtid.strip():
                raise ValueError(f'GTID domain {domain} appears in more than one source')
            domains[domain] = gtid.strip()
    return ','.join(domains[d] for d in sorted(domains))


def sql(statement, **kwargs):
    return subprocess.run(['mariadb', '-N', '-B'], input=statement, text=True, check=True,
                          capture_output=True, **kwargs).stdout


def quote(value):
    return "'" + value.replace('\\', '\\\\').replace("'", "\\'") + "'"


def main():
    password = os.environ['LAB_REPL_PASSWORD']
    hosts = {name: os.environ[variable] for name, (variable, _) in CHANNELS.items()}

    rows = sql('SHOW ALL SLAVES STATUS\\G')
    existing = set(re.findall(r'^\s*Connection_name: (\S*)$', rows, re.M))
    if existing == set(CHANNELS):
        sql('START ALL SLAVES')
        print('UNCHANGED')
        return
    if existing:
        raise SystemExit(f'warehouse has unexpected replication channels {sorted(existing)}; inspect it before reseeding.')

    present = sql("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME IN ('lab','orders')").split()
    if present:
        raise SystemExit(f'warehouse already contains {present} but has no channels; refusing to overwrite.')

    positions = []
    with tempfile.TemporaryDirectory(prefix='lab-warehouse-') as work:
        for name, (_, database) in CHANNELS.items():
            path = os.path.join(work, f'{name}.sql')
            with open(path, 'w') as output:
                # --single-transaction gives a consistent snapshot without locking the source.
                subprocess.run(['mariadb-dump', '-h', hosts[name], '-u', 'lab_dump', '--single-transaction',
                                '--gtid', '--master-data=2', '--triggers', '--databases', database],
                               stdout=output, check=True, env={**os.environ, 'MYSQL_PWD': password})
            with open(path) as dumped:
                positions.append(dump_position(dumped.read()))
            # Loading must not write local GTIDs, or strict mode would reject later replicated events.
            with open(path) as dumped:
                subprocess.run(['mariadb', '--init-command=SET SESSION sql_log_bin=0'], stdin=dumped, check=True)
            print(f'Seeded {database} from {hosts[name]} at {positions[-1]}', file=sys.stderr)

    merged = merge_positions(positions)
    statements = [f'SET GLOBAL gtid_slave_pos={quote(merged)};']
    for name in CHANNELS:
        statements.append(
            f"CHANGE MASTER {quote(name)} TO MASTER_HOST={quote(hosts[name])}, MASTER_PORT=3306, "
            f"MASTER_USER='lab_repl', MASTER_PASSWORD={quote(password)}, MASTER_USE_GTID=slave_pos;")
    statements.append('START ALL SLAVES;')
    sql('\n'.join(statements))
    print(f'Starting position {merged}', file=sys.stderr)
    print('CHANGED')


if __name__ == '__main__':
    main()
