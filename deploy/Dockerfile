# syntax=docker/dockerfile:1
# NEXUS — the deploy image (W3 part 3, run/specs/W3.md D1 and D6).
#
# Two stages: the venv built from requirements.txt as it is (every line, the
# test and build tools included: start.py's dependency check imports each
# one and names a missing one), then a slim runtime with a non-root user, the
# data root on a volume, a HEALTHCHECK that is a Python request to /healthz
# (the image has no curl), and `start.py --serve` as PID 1's command in exec
# form, so a `docker stop` reaches uvicorn itself and releases the store's
# lock (W2's F4, measured by W3's establish: a wrapper that does not exec
# leaves the lock for the next start to take over; no `init: true`).
#
# Learning is frozen by the image's environment (RUN_CHARTER law 12): the
# server's own default stays live, and the entrypoint's rule (frozen when
# nothing sets it) is what the image makes explicit. NEXUS_HOST=0.0.0.0
# binds inside the container only: compose keeps the container on an
# internal network with no default route, and Caddy publishes the console.
#
# Behind a TLS-intercepting proxy, pass its CA as a BUILD SECRET, never a
# layer:  docker build --secret id=proxyca,src=/path/ca.pem -f deploy/Dockerfile .
# Without the secret the mount is empty and pip verifies against its own CAs.
# The mount is 0444: BuildKit mounts a secret root's and 0400 by default, and
# apt fetches as its unprivileged `_apt` user, which could not read the CA, so
# `apt-get update` failed behind such a proxy (BACKLOG W6-B17). The secret is
# a public certificate and stays out of every layer either way.

FROM python:3.11-slim AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
COPY requirements.txt /tmp/requirements.txt
RUN --mount=type=secret,id=proxyca,target=/run/secrets/proxyca.crt,mode=0444 \
    sh -c 'if [ -s /run/secrets/proxyca.crt ]; then export PIP_CERT=/run/secrets/proxyca.crt; fi; \
           /opt/venv/bin/pip install -r /tmp/requirements.txt'

FROM python:3.11-slim AS runtime
LABEL org.opencontainers.image.title="NEXUS" \
      org.opencontainers.image.description="Local AI with persistent graph memory: the console (FastAPI) with its stores on /var/lib/nexus" \
      org.opencontainers.image.source="https://github.com/Slickfish/talima"
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NEXUS_HOST=0.0.0.0 \
    NEXUS_PORT=8000 \
    NEXUS_DATA_ROOT=/var/lib/nexus \
    NEXUS_LEARNING=frozen
RUN useradd --system --uid 10001 --user-group --home-dir /home/nexus --create-home nexus \
 && mkdir -p /var/lib/nexus && chown nexus:nexus /var/lib/nexus
# W3b part 5 (run/specs/W3b.md D11): the hosted store's backup is pg_dump and
# its restore verification pg_restore, createdb and dropdb, and pg_dump must
# be at least the server's major. So the image carries postgresql-client-16
# from the PostgreSQL apt repository (Debian's own ships another major): the
# major compose's `postgres` service runs (tests/test_w3b_backup.py K6 reads
# both). Behind a TLS-intercepting proxy the same build secret is apt's and
# curl's CA for these commands only, never a layer. Debian's mirror is read
# over https (deb.debian.org serves it; a proxy that refuses plain http, as
# the run's sandbox's does, then works too).
#
# The key fetch retries on every error (W4b's closing deploy smoke, run 28:
# the hosted job's build died here with curl's exit 56, a failed receive from
# www.postgresql.org, while the stub job built the same file). curl retries
# nothing unless asked, and --retry alone leaves a failed receive out; it
# writes with -o, so a retry starts the file again (a pipe or a > redirect
# would keep a failed attempt's bytes). apt retries each download three times
# by default (apt 2.3.2). The build stage's pip (24.0 today, from the base
# image's ensurepip) retries a connection that fails before its response
# five times, but makes one attempt at a body cut short (BACKLOG W4b-R4). So
# the key was the one fetch in the build's RUN lines with no retry at all
# (BuildKit's own pulls are not measured here).
# tests/test_w4b_image_fetch.py runs this command against a server that
# resets the first connection.
RUN --mount=type=secret,id=proxyca,target=/run/secrets/proxyca.crt,mode=0444 \
    sh -euc 'CA=""; if [ -s /run/secrets/proxyca.crt ]; then CA=/run/secrets/proxyca.crt; fi; \
             APT="apt-get ${CA:+-o Acquire::https::CaInfo=$CA}"; \
             sed -i "s|http://deb.debian.org|https://deb.debian.org|g" /etc/apt/sources.list.d/debian.sources; \
             $APT update; \
             $APT install -y --no-install-recommends ca-certificates curl; \
             install -d /usr/share/postgresql-common/pgdg; \
             curl -fsSL --retry 5 --retry-all-errors ${CA:+--cacert $CA} \
                  -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
                  https://www.postgresql.org/media/keys/ACCC4CF8.asc; \
             . /etc/os-release; \
             echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
                  > /etc/apt/sources.list.d/pgdg.list; \
             $APT update; \
             $APT install -y --no-install-recommends postgresql-client-16; \
             apt-get purge -y --auto-remove curl; \
             rm -rf /var/lib/apt/lists/*; \
             pg_dump --version'
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY --chown=nexus:nexus backend/ backend/
COPY --chown=nexus:nexus frontend/ frontend/
COPY --chown=nexus:nexus scripts/ scripts/
COPY --chown=nexus:nexus start.py requirements.txt .env.example ./
USER nexus
VOLUME /var/lib/nexus
EXPOSE 8000
# 200 means the server serves; the model server's state is REPORTED in the
# body (/healthz.model_server), never gated: a container whose model server
# is down is up, and says so.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; r = urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4); sys.exit(0 if r.status == 200 else 1)"]
CMD ["python", "start.py", "--serve", "--port", "8000", "--data-root", "/var/lib/nexus"]
