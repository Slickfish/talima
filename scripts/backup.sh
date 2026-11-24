#!/usr/bin/env sh
# W3 part 4 — the daily backup, as the compose `backup` service and the
# systemd timer run it (run/specs/W3.md D5). A thin wrapper over
# scripts/backup.py: the data root from NEXUS_DATA_ROOT (the image sets
# /var/lib/nexus), the destination from NEXUS_BACKUP_DIR (the compose
# service mounts /var/backups/nexus), the retention from NEXUS_BACKUP_KEEP
# (14 by default: two weeks of daily backups; unset it to keep every one).
# A hosted store (W3b part 5): NEXUS_BACKUP_DATABASE_URL, when set, names
# the read-only backup role's URL, and the store is dumped beside the data
# root's files; a data root that still holds kb.db or archive.db is refused
# by name (one backup holds one store: the delta check's D6). backup.py
# reads the setting from the environment this script passes
# on; it is never put on a command line, which another user's `ps` reads
# (W3b close, F6).
# Every argument given is passed on to backup.py after these.
set -eu
here=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
data_root=${NEXUS_DATA_ROOT:-"$here/data"}
out=${NEXUS_BACKUP_DIR:-"$here/backups"}
keep=${NEXUS_BACKUP_KEEP-14}
set -- --data-root "$data_root" --out "$out" "$@"
if [ -n "$keep" ]; then
  set -- "$@" --keep "$keep"
fi
exec python "$here/scripts/backup.py" "$@"
