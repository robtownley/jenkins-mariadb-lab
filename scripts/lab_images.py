#!/usr/bin/env python3
"""Report the image Packer just built and delete older lab images (and their snapshots).

  lab_images.py report-and-prune --keep N
"""
import argparse
import json
import os
import subprocess
from pathlib import Path


def run(*args):
    return subprocess.run(['aws', 'ec2', *args, '--output', 'json'], text=True, capture_output=True, check=True).stdout


def lab_images(version):
    images = json.loads(run('describe-images', '--owners', 'self', '--filters',
                            f'Name=name,Values=mariadb-lab-node-{version}-*'))['Images']
    return sorted(images, key=lambda image: image['CreationDate'], reverse=True)


def to_delete(images, keep):
    """Images beyond the newest `keep` (images sorted newest first)."""
    return images[keep:] if keep > 0 else []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['report-and-prune'])
    parser.add_argument('--keep', type=int, default=2)
    args = parser.parse_args()
    version = os.environ.get('LAB_MARIADB_VERSION', '11.4')

    manifest = json.loads(Path('packer-manifest.json').read_text())
    built = manifest['builds'][-1]['artifact_id']  # "eu-west-2:ami-..."
    old = to_delete(lab_images(version), args.keep)
    for image in old:
        run('deregister-image', '--image-id', image['ImageId'])
        for mapping in image.get('BlockDeviceMappings', []):
            snapshot = mapping.get('Ebs', {}).get('SnapshotId')
            if snapshot:
                run('delete-snapshot', '--snapshot-id', snapshot)
    report = [f'Built lab image {built} (MariaDB {version}).',
              f'Kept the newest {args.keep} lab images for {version}; deleted {len(old)} older one(s).',
              'Tick USE_LAB_IMAGE so NEW servers start from it. Existing servers are not replaced.']
    Path('build_image-report.txt').write_text('\n'.join(report) + '\n')
    print('\n'.join(report))


if __name__ == '__main__':
    main()
