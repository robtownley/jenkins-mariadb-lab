#!/bin/bash
# Usage: lab-load PROFILE MINUTES   (run by loadgen.yml as a transient systemd unit)
# Sends sysbench traffic through MaxScale; output goes to the journal and /var/log/lab-load.log.
set -euo pipefail
# shellcheck source=/dev/null
. /etc/lab-load.env   # MAXSCALE_HOST, LAB_APP_PASSWORD

profile=$1
seconds=$(( $2 * 60 ))
common=(--db-driver=mysql --mysql-host="$MAXSCALE_HOST" --mysql-port=3306
        --mysql-user=lab_app --mysql-password="$LAB_APP_PASSWORD" --mysql-db=sbtest
        --tables=4 --table-size=100000 --report-interval=10 --histogram=off)

run() {
    sysbench "$@" "${common[@]}" run
}

case "$profile" in
    prepare)   sysbench oltp_read_write "${common[@]}" --threads=4 prepare ;;
    light)     run oltp_read_write --threads=4 --rate=50 --time="$seconds" ;;
    heavy)     run oltp_read_write --threads=16 --time="$seconds" ;;
    # Autocommit reads, so MaxScale's readwritesplit can send them to replicas.
    read_only) run oltp_read_only --threads=8 --skip_trx=on --time="$seconds" ;;
    spiky)
        end=$(( SECONDS + seconds ))
        while (( SECONDS < end )); do
            slice=$(( end - SECONDS < 60 ? end - SECONDS : 60 ))
            run oltp_read_write --threads=2 --rate=20 --time="$slice"
            (( SECONDS < end )) || break
            slice=$(( end - SECONDS < 60 ? end - SECONDS : 60 ))
            run oltp_read_write --threads=16 --time="$slice"
        done
        ;;
    *) echo "Unknown profile: $profile" >&2; exit 2 ;;
esac
