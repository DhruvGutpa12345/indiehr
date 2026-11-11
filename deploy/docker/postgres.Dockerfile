# PostgreSQL 18.6 with pgBackRest for the staging stack (P1-T8c). The same official Alpine image
# the stack used before (same data layout, same user 70), plus pgBackRest 2.60.0 built from its
# release source, pinned by SHA-256 (Alpine packages only 2.58). Build:
#   docker buildx build -f deploy/docker/postgres.Dockerfile -t <tag> deploy/docker
ARG PGBACKREST_VERSION=2.60.0

FROM postgres:18.6-alpine3.23@sha256:f1031bf693d8d60b0a0380c51a3510607c7a93c7c1d18bf7af94a3a8dbeb40b8 AS build
ARG PGBACKREST_VERSION
ADD --checksum=sha256:7409309d5b69365ecc21f3f9c377056a6a4cdc327ce062e41354a442989d22d0 \
    https://github.com/pgbackrest/pgbackrest/archive/refs/tags/release/${PGBACKREST_VERSION}.tar.gz /src.tar.gz
# Build tools only in this stage; libpq comes from the official image itself (/usr/local)
RUN apk add --no-cache build-base meson ninja pkgconf openssl-dev libxml2-dev lz4-dev zstd-dev \
      zlib-dev bzip2-dev yaml-dev curl-dev icu-dev py3-yaml \
 && mkdir /src && tar -xzf /src.tar.gz -C /src --strip-components=1 \
 && cd /src \
 && PKG_CONFIG_PATH=/usr/local/lib/pkgconfig meson setup build --buildtype=release \
      -Dlibssh2=disabled -Dlibsystemd=disabled \
 && ninja -C build \
 && strip build/src/pgbackrest \
 && build/src/pgbackrest version

FROM postgres:18.6-alpine3.23@sha256:f1031bf693d8d60b0a0380c51a3510607c7a93c7c1d18bf7af94a3a8dbeb40b8
ARG PGBACKREST_VERSION
LABEL org.opencontainers.image.title="indiehr-postgres" \
      ihr.pgbackrest.version="${PGBACKREST_VERSION}"
# pgBackRest's runtime libraries (libpq is the image's own). krb5-libs at least 1.22.3-r0:
# the base image still has 1.22.1-r0 (18 HIGH advisories, 2026-11-11); the build fails if the
# fixed package is not available
RUN apk add --no-cache libxml2 lz4-libs zstd-libs zlib libbz2 yaml 'krb5-libs>=1.22.3-r0' \
 && install -d -o postgres -g postgres -m 750 /var/lib/pgbackrest /var/spool/pgbackrest /tmp/pgbackrest
COPY --from=build /src/build/src/pgbackrest /usr/local/bin/pgbackrest
# archive_command (database) and the schedule (backup service), P1-T8c; the spool belongs to
# postgres so a new named volume mounted there does too
COPY --chmod=755 backup/wal-spool.sh /usr/local/bin/ihr-pgbackrest-spool
COPY --chmod=755 backup/schedule.sh /usr/local/bin/ihr-backup-schedule
COPY --chmod=755 backup/health.sh /usr/local/bin/ihr-backup-health
COPY --chmod=755 backup/spool-sweep.sh /usr/local/bin/ihr-backup-spool-sweep
RUN install -d -o postgres -g postgres -m 700 /var/spool/ihr-wal \
 # gosu only drops root to postgres; this image always runs as postgres (user 70), and the
 # official build's gosu carries known Go vulnerabilities
 && rm -f /usr/local/bin/gosu
USER postgres
RUN pgbackrest version
# MIT licence of pgBackRest travels with its binary
COPY --from=build /src/LICENSE /usr/share/doc/pgbackrest/LICENSE
