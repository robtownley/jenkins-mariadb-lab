#!/bin/bash
# Installed on a replica as /usr/local/sbin/lab-restore-check by lab_backup.py restore-test.
# Reads a gzipped xbstream backup on stdin, restores it into a scratch directory, starts a throwaway
# MariaDB on a private socket (no networking, no replication), checks the data, then deletes everything.
set -euo pipefail

work=$(mktemp -d /var/tmp/lab-restore.XXXXXX)
pid=
cleanup() {
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        mariadb-admin --socket="$work/mysqld.sock" shutdown 2>/dev/null || kill "$pid"
        wait "$pid" 2>/dev/null || true
    fi
    rm -rf "$work"
}
trap cleanup EXIT

mkdir "$work/data"
gunzip | mbstream -x -C "$work/data"
if ! mariadb-backup --prepare --target-dir="$work/data" >"$work/prepare.log" 2>&1; then
    tail -n 30 "$work/prepare.log" >&2
    exit 1
fi
echo "CHECK Prepared $(du -sh "$work/data" | cut -f1) of data"
for info in mariadb_backup_slave_info xtrabackup_slave_info; do
    if [ -f "$work/data/$info" ]; then
        echo "CHECK Backup replication position: $(tr -s '\n' ' ' < "$work/data/$info")"
        break
    fi
done

chown -R mysql:mysql "$work"
/usr/sbin/mariadbd --no-defaults --user=mysql --datadir="$work/data" --socket="$work/mysqld.sock" \
    --pid-file="$work/mysqld.pid" --log-error="$work/error.log" --skip-networking --skip-log-bin \
    --skip-slave-start --server-id=999 --innodb-buffer-pool-size=128M --event-scheduler=OFF &
pid=$!
for _ in $(seq 60); do
    mariadb-admin --socket="$work/mysqld.sock" ping >/dev/null 2>&1 && break
    sleep 2
done
if ! mariadb-admin --socket="$work/mysqld.sock" ping >/dev/null 2>&1; then
    tail -n 30 "$work/error.log" >&2
    exit 1
fi

q() { mariadb --socket="$work/mysqld.sock" -N -B -e "$1"; }
echo "CHECK Databases: $(q "SELECT GROUP_CONCAT(SCHEMA_NAME ORDER BY SCHEMA_NAME) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME NOT IN ('mysql','information_schema','performance_schema','sys')")"
markers=$(q "SELECT COUNT(*) FROM lab.pipeline_checks")
echo "CHECK lab.pipeline_checks rows: $markers"
status=$(q "CHECK TABLE lab.pipeline_checks" | awk '{print $NF}' | tail -n 1)
echo "CHECK CHECK TABLE lab.pipeline_checks: $status"
if q "SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA='sbtest' AND TABLE_NAME='sbtest1'" | grep -q 1; then
    echo "CHECK sbtest.sbtest1 rows: $(q "SELECT COUNT(*) FROM sbtest.sbtest1")"
fi
[ "$markers" -gt 0 ] && [ "$status" = OK ]
echo RESTORE_OK
