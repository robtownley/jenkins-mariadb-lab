#!/usr/bin/env python3
"""Probe a replica or snapshot a cleanly stopped donor; never reset its channel."""
import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import pymysql

WORK = Path('/data/backup_source/.jenkins-seed-current')

def run(args, **kw):
    return subprocess.run(args, check=True, **kw)

def connect():
    return pymysql.connect(user='root', unix_socket='/run/mysqld/mysqld.sock',
                           autocommit=True, cursorclass=pymysql.cursors.DictCursor)

def query(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()

def healthy(conn):
    rows = query(conn, 'SHOW SLAVE STATUS')
    if len(rows) != 1:
        raise RuntimeError('Exactly one replication channel required')
    row = rows[0]
    if not (row['Slave_IO_Running'] == row['Slave_SQL_Running'] == 'Yes'
            and row['Seconds_Behind_Master'] is not None
            and int(row['Seconds_Behind_Master']) == 0
            and int(row['Last_IO_Errno']) == int(row['Last_SQL_Errno']) == 0):
        raise RuntimeError('Replica threads, lag or errors are unhealthy')
    return row

def storage():
    report = run(['zpool', 'status', 'data'], capture_output=True, text=True).stdout
    if 'errors: No known data errors' not in report:
        raise RuntimeError('Pool reports data errors')
    for line in report.splitlines():
        fields = line.split()
        if len(fields) >= 5 and fields[1] == 'ONLINE' and all(v.isdigit() for v in fields[2:5]):
            if any(int(v) for v in fields[2:5]):
                raise RuntimeError('Pool reports device errors')
    state = run(['zpool', 'list', '-H', '-o', 'health', 'data'], capture_output=True, text=True).stdout.strip()
    if state != 'ONLINE':
        raise RuntimeError('Pool is not ONLINE')
    for suffix in ['', '/.ibd', '/.log', '/.tmp']:
        run(['findmnt', '-rn', '-t', 'zfs', '-S', 'data/mysql' + suffix,
             '-M', '/var/lib/mysql' + suffix], capture_output=True)

def position(conn, replica):
    row = query(conn, 'SELECT @@GLOBAL.gtid_slave_pos AS applied, '
                '@@GLOBAL.gtid_current_pos AS current_pos, '
                '@@GLOBAL.gtid_binlog_pos AS binlog, @@read_only AS ro')[0]
    value = row['applied'] if replica else row['binlog']
    if not re.fullmatch(r'[0-9]+-[0-9]+-[0-9]+(?:,[0-9]+-[0-9]+-[0-9]+)*', value):
        raise RuntimeError('Invalid seed GTID')
    if replica and (not row['ro'] or set(value.split(',')) != set(row['current_pos'].split(','))):
        raise RuntimeError('Replica has local GTIDs or is writable')
    return value

def snapshot(replica):
    storage()
    if WORK.exists():
        raise RuntimeError('Seed workspace already exists')
    WORK.mkdir(mode=0o700)
    conn = connect()
    stopped = False
    replica_stopped = False
    label = 'jenkins-seed-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    snap = 'data/mysql@' + label
    try:
        if replica:
            healthy(conn)
            replica_stopped = True
            query(conn, 'STOP SLAVE')
        query(conn, 'SET GLOBAL innodb_fast_shutdown = 0')
        query(conn, 'FLUSH TABLES WITH READ LOCK')
        gtid = position(conn, replica)
        version = query(conn, 'SELECT VERSION() AS version')[0]['version']
        redo = query(conn, 'SELECT @@innodb_log_group_home_dir AS redo')[0]['redo']
        if redo not in ('.', './', '/var/lib/mysql', '/var/lib/mysql/', '/var/lib/mysql/.ibd', '/var/lib/mysql/.ibd/'):
            raise RuntimeError('Unexpected redo location')
        stopped = True
        run(['systemctl', 'stop', 'mariadb'], timeout=300)
        if subprocess.run(['pgrep', '-x', 'mariadbd'], capture_output=True).returncode != 1:
            raise RuntimeError('MariaDB remains running')
        run(['zfs', 'snapshot', '-r', snap])
    finally:
        try:
            conn.close()
        except Exception:
            pass
        if stopped:
            run(['systemctl', 'start', 'mariadb'], timeout=300)
        if replica_stopped:
            recovery = connect()
            try:
                query(recovery, 'START SLAVE')
            finally:
                recovery.close()
    result = {'gtid': gtid, 'version': version, 'snapshot': snap,
              'redo_subdir': '.ibd' if '.ibd' in redo else '', 'method': 'zfs'}
    (WORK / 'manifest.json').write_text(json.dumps(result))
    print(json.dumps(result))


def send_stream():
    # Called after Ansible has verified recovery and restored MaxScale routing.
    manifest = json.loads((WORK / 'manifest.json').read_text())
    snap = manifest['snapshot']
    if not re.fullmatch(r'data/mysql@jenkins-seed-[0-9]+T[0-9]+Z', snap):
        raise RuntimeError('Unexpected snapshot name')
    estimate = run(['zfs', 'send', '-nP', '-R', snap], capture_output=True, text=True)
    sizes = re.findall(r'^size\s+([0-9]+)$', estimate.stdout + '\n' + estimate.stderr, re.M)
    if not sizes:
        raise RuntimeError('Cannot determine recursive stream size')
    available = int(run(['zfs', 'get', '-Hp', '-o', 'value', 'available', 'data/backup_source'],
                        capture_output=True, text=True).stdout.strip())
    if available <= int(sizes[-1]) + 1073741824:
        raise RuntimeError('Insufficient donor capacity for recursive stream')
    stream = WORK / 'seed.zfs'
    with stream.open('wb') as output:
        run(['zfs', 'send', '-R', snap], stdout=output)
    stream.chmod(0o600)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['probe', 'snapshot', 'send'])
    parser.add_argument('--primary', action='store_true')
    args = parser.parse_args()
    if args.mode == 'send':
        send_stream()
        return
    if args.mode == 'snapshot':
        snapshot(not args.primary)
        return
    try:
        storage()
        conn = connect()
        try:
            row = healthy(conn) if not args.primary else {}
            position(conn, not args.primary)
            version = query(conn, 'SELECT VERSION() AS version')[0]['version']
        finally:
            conn.close()
        result = {'eligible': True, 'primary': row.get('Master_Host', ''), 'version': version}
    except Exception as exc:
        result = {'eligible': False, 'reason': str(exc)}
    print(json.dumps(result))

if __name__ == '__main__':
    main()
