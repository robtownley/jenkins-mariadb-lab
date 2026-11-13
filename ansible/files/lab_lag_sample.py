#!/usr/bin/python3
"""Sample this node's replication channels for SECONDS and print one JSON summary line.

Usage: lab-lag-sample SECONDS [INTERVAL]
"""
import json
import socket
import subprocess
import sys
import time


def channels():
    out = subprocess.run(['mariadb', '-B', '-e', 'SHOW ALL SLAVES STATUS'], text=True,
                         capture_output=True, timeout=10)
    if out.returncode != 0:
        return None
    lines = out.stdout.splitlines()
    return [dict(zip(lines[0].split('\t'), line.split('\t'))) for line in lines[1:]] if lines else []


def summarise(samples):
    """samples: list of channel lists (None when MariaDB did not answer)."""
    result = {'samples': len(samples), 'unreachable_samples': 0, 'channels': {}}
    for sample in samples:
        if sample is None:
            result['unreachable_samples'] += 1
            continue
        for channel in sample:
            name = channel.get('Connection_name') or 'default'
            stats = result['channels'].setdefault(name, {'max_lag': 0, 'broken_samples': 0, 'last_lag': None})
            lag = channel.get('Seconds_Behind_Master')
            running = channel.get('Slave_IO_Running') == 'Yes' and channel.get('Slave_SQL_Running') == 'Yes'
            if not running or lag in (None, 'NULL'):
                stats['broken_samples'] += 1
                stats['last_lag'] = None
            else:
                stats['max_lag'] = max(stats['max_lag'], int(lag))
                stats['last_lag'] = int(lag)
    return result


def main():
    seconds = int(sys.argv[1])
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    deadline = time.monotonic() + seconds
    samples = []
    while True:
        samples.append(channels())
        if time.monotonic() >= deadline:
            break
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
    print(json.dumps({'node': socket.gethostname(), **summarise(samples)}))


if __name__ == '__main__':
    main()
