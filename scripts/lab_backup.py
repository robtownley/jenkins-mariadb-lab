#!/usr/bin/env python3
"""Stream mariadb-backup of a replica to S3, and restore-test the newest backup.

  lab_backup.py backup --keep N    back up the last intermediate replica, keep the N newest backups
  lab_backup.py restore-test       restore the newest backup into a scratch directory on that replica and check it

Data flows replica -> ssh -> Jenkins -> aws s3 cp, so the lab servers need no AWS credentials.
"""
import argparse
import datetime
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lab_ssh import last_intermediate_replica, ssh_command  # noqa: E402

KEY = re.compile(r'^(\d{8}T\d{6}Z)-([a-z0-9-]+)\.xbstream\.gz$')
CHUNK = 1 << 20


def s3_uri():
    uri = os.environ.get('BACKUP_S3_URI', '').rstrip('/')
    if not uri.startswith('s3://'):
        raise SystemExit('BACKUP_S3_URI must be set to an s3:// prefix')
    return uri


def list_backups(uri):
    """Backup object names under the prefix, oldest first."""
    out = subprocess.run(['aws', 's3', 'ls', uri + '/'], text=True, capture_output=True)
    if out.returncode not in (0, 1):  # 1 = nothing there yet
        raise SystemExit(out.stderr)
    names = [line.split()[-1] for line in out.stdout.splitlines() if line.strip()]
    return sorted(name for name in names if KEY.match(name))


def to_prune(names, keep):
    return names[:-keep] if keep > 0 and len(names) > keep else []


def pipe(producer, consumer):
    """Run producer | consumer, returning (bytes copied, producer rc, consumer rc)."""
    source = subprocess.Popen(producer, stdout=subprocess.PIPE)
    sink = subprocess.Popen(consumer, stdin=subprocess.PIPE)
    copied = 0
    try:
        while True:
            chunk = source.stdout.read(CHUNK)
            if not chunk:
                break
            sink.stdin.write(chunk)
            copied += len(chunk)
    except BrokenPipeError:
        pass
    finally:
        sink.stdin.close()
    return copied, source.wait(), sink.wait()


def backup(keep):
    uri = s3_uri()
    node = last_intermediate_replica()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    name = f'{stamp}-{node}.xbstream.gz'
    # --safe-slave-backup pauses the SQL thread only for the final non-InnoDB copy; --slave-info records the GTID.
    remote = ("sudo bash -o pipefail -c 'mkdir -p /var/tmp/lab-backup && "
              "mariadb-backup --backup --stream=xbstream --target-dir=/var/tmp/lab-backup "
              "--slave-info --safe-slave-backup 2>/var/tmp/lab-backup.log | gzip -1'")
    print(f'Backing up {node} to {uri}/{name}', flush=True)
    copied, ssh_rc, aws_rc = pipe(ssh_command(node, remote),
                                  ['aws', 's3', 'cp', '-', f'{uri}/{name}', '--sse', 'AES256', '--only-show-errors'])
    if ssh_rc != 0 or aws_rc != 0:
        subprocess.run(ssh_command(node, 'sudo tail -n 30 /var/tmp/lab-backup.log'))
        subprocess.run(['aws', 's3', 'rm', f'{uri}/{name}', '--only-show-errors'])
        raise SystemExit(f'Backup failed (ssh/mariadb-backup rc={ssh_rc}, aws rc={aws_rc}); partial upload removed.')

    pruned = to_prune(list_backups(uri), keep)
    for old in pruned:
        subprocess.run(['aws', 's3', 'rm', f'{uri}/{old}', '--only-show-errors'], check=True)
    report = [f'Backup of {node}: {uri}/{name} ({copied / 1e6:,.1f} MB compressed)',
              f'Kept the newest {keep}; removed {len(pruned)} older backup(s).']
    Path('backup-report.txt').write_text('\n'.join(report) + '\n')
    print('\n'.join(report))


def restore_test():
    uri = s3_uri()
    names = list_backups(uri)
    if not names:
        raise SystemExit(f'No backups under {uri}; run ACTION=BACKUP first.')
    name = names[-1]
    node = last_intermediate_replica()
    checker = (Path(__file__).resolve().parent / 'restore_check.sh').read_text()
    subprocess.run(ssh_command(node, 'sudo install -m 0700 /dev/stdin /usr/local/sbin/lab-restore-check'),
                   input=checker.encode(), check=True)
    print(f'Restoring {uri}/{name} on {node} into a scratch directory', flush=True)
    result = subprocess.run(['bash', '-o', 'pipefail', '-c',
                             'aws s3 cp "$1" - --only-show-errors | "${@:2}"', '_',
                             f'{uri}/{name}', *ssh_command(node, 'sudo /usr/local/sbin/lab-restore-check')],
                            text=True, capture_output=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    checks = [line[len('CHECK '):] for line in result.stdout.splitlines() if line.startswith('CHECK ')]
    status = 'PASSED' if result.returncode == 0 and 'RESTORE_OK' in result.stdout else 'FAILED'
    report = [f'Restore test {status}: {uri}/{name} restored on {node} (scratch copy, then deleted)', *checks]
    Path('restore_test-report.txt').write_text('\n'.join(report) + '\n')
    if status != 'PASSED':
        raise SystemExit('Restore test failed; see the output above.')


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    backup_parser = sub.add_parser('backup')
    backup_parser.add_argument('--keep', type=int, default=7)
    sub.add_parser('restore-test')
    args = parser.parse_args()
    if args.command == 'backup':
        backup(args.keep)
    else:
        restore_test()


if __name__ == '__main__':
    main()
