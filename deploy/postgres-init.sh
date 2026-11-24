#!/bin/sh
# W4 part 0 (run/specs/W4.md D14; BACKLOG W3b-F8): the hosted store's roles
# and databases, made once, when the `postgres` service's volume is first
# initialised. The official image runs /docker-entrypoint-initdb.d/*.sh then,
# as its superuser, and never again on an initialised volume: a volume keeps
# the roles it has (docs/DEPLOY.md says how a volume made before W4 moves).
#
# The superuser is the image's own `postgres` (POSTGRES_USER). This script
# runs as it, and nothing connects as it afterwards. For each key in
# NEXUS_TESTERS (space-separated; compose passes t1 unless told otherwise):
#
#   - a role nexus_<key>, which logs in and holds no superuser, CREATEDB,
#     CREATEROLE, REPLICATION or BYPASSRLS, its password from
#     NEXUS_<KEY>_PASSWORD (the key in upper case). NEXUS connects as it;
#   - a database nexus_<key>, which that role owns. NEXUS creates the
#     store's tables there at first open. CONNECT on it is revoked from
#     PUBLIC and granted to the backup role, so the owner and the backup
#     role are the only roles that may open it: one tester's role cannot
#     open another tester's database.
#
# And one backup role, NEXUS_BACKUP_ROLE (nexus_backup unless set): LOGIN
# CREATEDB, its password from NEXUS_BACKUP_PASSWORD. In each tester's
# database it has USAGE on the schema public and SELECT on every table the
# tester's role creates there, now or later (default privileges), so the one
# backup role reads every tester's store. CREATEDB is for
# scripts/restore_verify.py, which restores a dump into scratch databases
# and drops them. It holds nothing that writes a store, so
# scripts/_readonly.py's privilege check passes it.
#
# Before anything is made, the script stops by name (one line on stderr,
# exit 1) when NEXUS_BACKUP_PASSWORD is unset or empty, when NEXUS_TESTERS
# is empty, when a key is not lower-case letters, digits and underscores or
# is longer than 57 characters (a role's name holds 63), when
# NEXUS_BACKUP_ROLE is not such a name or is the superuser's, when a role
# would be named twice (a key given twice, or a tester's role named like the
# backup role or the superuser: the failing CREATE ROLE would be logged by
# the server with its password), or when a tester's password variable is
# unset or empty. A stop leaves the volume initialised without these roles,
# and the image does not run the script again (docs/DEPLOY.md).
#
# Passwords reach psql through its environment (\getenv), never on its
# command line; the role names reach it as psql variables, quoted as
# :"name". It connects with psql --username "$POSTGRES_USER" --dbname
# "$POSTGRES_DB", so PGHOST, PGPORT and PGPASSWORD let it run against a
# server over TCP (tests/test_w4_hosted.py does). \getenv needs psql 15 or
# later; the image's is 16.
set -eu
set -f    # NEXUS_TESTERS is split into words, and a word is never a file pattern

fail() {
  echo "postgres-init: $*" >&2
  exit 1
}

is_name() {
  case $1 in
    '' | *[!abcdefghijklmnopqrstuvwxyz0123456789_]*) return 1 ;;
  esac
}

upper() {
  printf '%s' "$1" | tr abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ
}

psql_superuser() {
  psql -v ON_ERROR_STOP=1 --no-psqlrc --no-password --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" "$@"
}

backup_role=${NEXUS_BACKUP_ROLE:-nexus_backup}
[ -n "${NEXUS_BACKUP_PASSWORD:-}" ] ||
  fail "NEXUS_BACKUP_PASSWORD is not set: the read-only backup role ($backup_role) needs one"
is_name "$backup_role" ||
  fail "NEXUS_BACKUP_ROLE='$backup_role' is not a role name of lower-case letters, digits and underscores"
[ "$backup_role" != "$POSTGRES_USER" ] ||
  fail "NEXUS_BACKUP_ROLE names the superuser ($POSTGRES_USER): the backup role must be a role of its own"

# shellcheck disable=SC2086  # split into words on purpose; set -f above keeps each word literal
set -- ${NEXUS_TESTERS:-}
[ $# -gt 0 ] || fail "NEXUS_TESTERS is empty: name at least one tester key (compose passes t1)"
named=" $POSTGRES_USER $backup_role "
password=
for key in "$@"; do
  is_name "$key" ||
    fail "the tester key '$key' is not lower-case letters, digits and underscores ([a-z0-9_]+)"
  [ ${#key} -le 57 ] ||
    fail "the tester key '$key' is longer than 57 characters: the role nexus_$key would not fit a role name (63)"
  case $named in
    *" nexus_$key "*)
      fail "the role nexus_$key would be named twice (a key given twice, or the backup role's or the superuser's name)" ;;
  esac
  named="$named nexus_$key "
  variable="NEXUS_$(upper "$key")_PASSWORD"
  eval "password=\${$variable:-}"    # the key was checked above: the variable's name is letters, digits and _
  [ -n "$password" ] || fail "$variable is not set: the tester role nexus_$key needs a password"
done
password=

psql_superuser --set=backup_role="$backup_role" <<'SQL' ||
\getenv backup_password NEXUS_BACKUP_PASSWORD
CREATE ROLE :"backup_role" LOGIN CREATEDB PASSWORD :'backup_password';
SQL
  fail "the backup role $backup_role could not be made (psql's message is above)"

made=
for key in "$@"; do
  psql_superuser --set=tester="nexus_$key" --set=backup_role="$backup_role" \
                 --set=password_variable="NEXUS_$(upper "$key")_PASSWORD" <<'SQL' ||
\getenv tester_password :password_variable
CREATE ROLE :"tester" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'tester_password';
CREATE DATABASE :"tester" OWNER :"tester";
REVOKE ALL ON DATABASE :"tester" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"tester" TO :"backup_role";
\connect :"tester"
GRANT USAGE ON SCHEMA public TO :"backup_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"tester" IN SCHEMA public GRANT SELECT ON TABLES TO :"backup_role";
SQL
    fail "the tester role and database nexus_$key could not be made (psql's message is above)"
  made="$made nexus_$key"
done
echo "postgres-init: made the backup role $backup_role (LOGIN CREATEDB) and, for each tester, a role and the database it owns, which only that role and $backup_role may open:$made"
