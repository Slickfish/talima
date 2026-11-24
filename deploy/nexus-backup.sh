#!/bin/sh
# NEXUS — the daily backup on a compose host (W4 part 3, run/specs/W4.md
# D18). deploy/cloud-init.yaml's nexus-backup.service runs it as root, from
# the compose project's folder (/opt/nexus), once a day.
#
# One backup holds one store. Under the `hosted` profile each tester's store
# has a backup service of its own (backup-t1, backup-t2 and so on), asked of
# compose here, so a tester added to docker-compose.yml is backed up with no
# edit to this script. Each one runs even when another has failed, and the
# run fails (exit 1) naming every one that failed. Under every other profile
# the one `backup` service runs.
#
# The profile is NEXUS_PROFILE in the project's .env, read the way
# cloud-init's own commands read that file: by the shell, so a comment after
# a value is a comment.
set -eu
cd -- "$(dirname -- "$0")/.."
set -a
# shellcheck source=/dev/null  # the operator's .env, written by cloud-init
. ./.env
set +a
profile=${NEXUS_PROFILE:?is not set in the .env of the compose project}
if [ "$profile" = hosted ]; then
  services=$(docker compose --profile hosted --profile backup config --services | grep -x 'backup-.*' || true)
  [ -n "$services" ] || { echo "nexus-backup: the hosted profile names no backup-<tester> service" >&2; exit 1; }
else
  services=backup
fi
failed=
for service in $services; do
  docker compose --profile "$profile" --profile backup run --rm "$service" || failed="$failed $service"
done
if [ -n "$failed" ]; then
  echo "nexus-backup: failed:$failed" >&2
  exit 1
fi
