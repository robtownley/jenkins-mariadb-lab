#!/usr/bin/env python3

import hashlib
import os
import re
import shutil
from pathlib import Path

ROOT = Path("/var/lib/mysql")
TARGET = ROOT / ".log"

# Include MariaDB's numbered logs and optional binlog .idx sidecars.
NUMBERED = re.compile(r"mariadb-(?:bin|relay)\.[0-9]+")
LOG_FILE = re.compile(
    r"mariadb-(?:bin|relay)\.(?:[0-9]+(?:\.idx)?|index)"
)


def checksum(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.digest()


def resolve_old_log(value):
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path

    if path.parent != ROOT or not NUMBERED.fullmatch(path.name):
        raise RuntimeError(f"Unexpected log reference: {value}")

    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"Missing or unexpected log: {path}")

    return path


def main():
    if not TARGET.is_dir():
        raise RuntimeError("Log dataset directory is missing.")

    files = sorted(
        path for path in ROOT.iterdir()
        if LOG_FILE.fullmatch(path.name)
    )

    if not files:
        raise RuntimeError("No original logs found.")

    indexes = {}
    indexed_names = set()

    # Complete validation before copying or editing anything.
    for source in files:
        destination = TARGET / source.name

        if source.is_symlink() or not source.is_file():
            raise RuntimeError(f"Unexpected source: {source}")

        if destination.exists() or destination.is_symlink():
            raise RuntimeError(
                f"Destination already exists: {destination}. "
                "Inspect a previous attempt before retrying."
            )

        if source.suffix == ".index":
            entries = []
            for line in source.read_text().splitlines():
                if not line.strip():
                    continue

                log = resolve_old_log(line.strip())
                indexed_names.add(log.name)
                entries.append(str(TARGET / log.name))

            indexes[source.name] = "".join(
                entry + "\n" for entry in entries
            )

    if "mariadb-bin.index" not in indexes:
        raise RuntimeError("Binary log index is missing.")

    metadata = ROOT / "relay-log.info"
    metadata_lines = None

    if metadata.exists():
        if metadata.is_symlink():
            raise RuntimeError("Unexpected relay metadata symlink.")

        metadata_lines = metadata.read_text().splitlines()

        # Expected format for this lab's MariaDB 11.4 default channel.
        if (
            len(metadata_lines) < 6
            or metadata_lines[0] != "5"
        ):
            raise RuntimeError("Unexpected relay metadata format.")

        relay = resolve_old_log(metadata_lines[1])

        if relay.name not in indexed_names:
            raise RuntimeError(
                "Saved relay log is absent from the indexes."
            )

        if relay.stat().st_size < int(metadata_lines[2]):
            raise RuntimeError("Saved relay position exceeds file size.")

        metadata_lines[1] = str(TARGET / relay.name)

        backup = ROOT / "relay-log.info.before-zfs-log-migration"
        if backup.exists():
            raise RuntimeError("Relay metadata backup already exists.")

    # Copy all files, retain original files, and verify the copies.
    for source in files:
        destination = TARGET / source.name
        stat = source.stat()

        shutil.copy2(source, destination)
        os.chown(destination, stat.st_uid, stat.st_gid)

        if checksum(source) != checksum(destination):
            raise RuntimeError(f"Copy verification failed: {source}")

    # These destination indexes now reference the copied log files.
    for name, contents in indexes.items():
        (TARGET / name).write_text(contents)

    if metadata_lines is not None:
        backup = ROOT / "relay-log.info.before-zfs-log-migration"
        shutil.copy2(metadata, backup)
        stat = metadata.stat()
        os.chown(backup, stat.st_uid, stat.st_gid)

        metadata.write_text("\n".join(metadata_lines) + "\n")

    print("Log copies verified; indexes and relay metadata updated.")


if __name__ == "__main__":
    main()
