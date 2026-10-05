#!/usr/bin/env python3
"""Create a clean physical lab seed, holding a read lock until shutdown."""
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pymysql

SOURCE = Path('/var/lib/mysql')
TARGET = Path('/data/backup_source/.jenkins-seed-current/backup')


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.digest()


def main():
    if TARGET.exists():
        raise RuntimeError('Seed destination already exists.')

    connection = pymysql.connect(
        unix_socket='/run/mysqld/mysqld.sock', user='root', autocommit=True
    )
    stop_requested = False
    try:
        with connection.cursor() as cursor:
            cursor.execute('SET GLOBAL innodb_fast_shutdown = 0')
            cursor.execute('FLUSH TABLES WITH READ LOCK')
            cursor.execute('SELECT @@GLOBAL.gtid_binlog_pos, @@innodb_log_group_home_dir, @@datadir')
            gtid, redo_directory, datadir = cursor.fetchone()
            if Path(datadir).resolve() != SOURCE:
                raise RuntimeError('Unexpected source datadir.')
            if not re.fullmatch(r'[0-9]+-[0-9]+-[0-9]+(?:,[0-9]+-[0-9]+-[0-9]+)*', gtid):
                raise RuntimeError('Primary has no valid binlog GTID position.')
            redo_directory = Path(redo_directory or '.')
            if not redo_directory.is_absolute():
                redo_directory = SOURCE / redo_directory
            redo = redo_directory.resolve() / 'ib_logfile0'
            if redo not in (SOURCE / 'ib_logfile0', SOURCE / '.ibd/ib_logfile0'):
                raise RuntimeError('Unexpected redo path.')
            if not redo.is_file() or redo.is_symlink():
                raise RuntimeError('Active redo file is missing or a symlink.')

            # This connection retains the global read lock while systemd stops
            # the server. There is no unlocked gap between recording GTID and stop.
            stop_requested = True
            run(['systemctl', 'stop', 'mariadb'], timeout=300)

        if subprocess.run(['pgrep', '-x', 'mariadbd'], capture_output=True).returncode != 1:
            raise RuntimeError('MariaDB processes remain after shutdown.')

        TARGET.mkdir(mode=0o700)
        excludes = [
            '--exclude=/ib_logfile0', '--exclude=/.ibd/ib_logfile0',
            '--exclude=/.log/', '--exclude=/.tmp/', '--exclude=/ibtmp1',
            '--exclude=/mariadb-bin.*', '--exclude=/mariadb-relay.*',
            '--exclude=/master.info*', '--exclude=/relay-log.info*',
            '--exclude=/multi-master.info',
        ]
        run(['rsync', '-aHAX', '--numeric-ids', *excludes,
             str(SOURCE) + '/', str(TARGET) + '/'])
        differences = run(
            ['rsync', '-aHAXnc', '--numeric-ids', '--itemize-changes',
             *excludes, str(SOURCE) + '/', str(TARGET) + '/'],
            capture_output=True, text=True
        ).stdout
        if differences.strip():
            raise RuntimeError('Cold data copy did not verify: ' + differences)

        # Normalize the active redo location in the archive. Fresh replicas
        # initially use the root datadir, then the pipeline migrates redo to ZFS.
        destination = TARGET / 'ib_logfile0'
        shutil.copy2(redo, destination)
        if digest(redo) != digest(destination):
            raise RuntimeError('Redo copy did not verify.')
        (TARGET / 'seed-gtid.txt').write_text(gtid + '\n')
    finally:
        try:
            connection.close()
        except Exception:
            pass
        if stop_requested:
            run(['systemctl', 'start', 'mariadb'], timeout=300)

    print(json.dumps({'gtid': gtid}))


if __name__ == '__main__':
    main()
