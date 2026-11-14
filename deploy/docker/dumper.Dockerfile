# The nightly per-hospital dumper (P1-T22a; deploy/dumps/dumper.py, compose.dumps.yaml): the
# same official Postgres 18.6 Alpine image as the database (pg_dump and pg_restore match the
# server; initdb and pg_ctl for the check's throwaway server), plus Python and the three
# libraries the dumper uses, all from Alpine 3.23's own packages (cryptography: the sealer;
# boto3: the bucket; psycopg: the snapshot). No pgBackRest, no gosu; runs as postgres (70). The
# dumper's code is mounted read-only from deploy/dumps and deploy/lib, as the log archiver's is.
# Build (scripts/deploy.sh, or scripts/staging.sh for a proof; labelled with this file's hash):
#   docker buildx build -f deploy/docker/dumper.Dockerfile -t <tag> deploy/docker
FROM postgres:18.6-alpine3.23@sha256:f1031bf693d8d60b0a0380c51a3510607c7a93c7c1d18bf7af94a3a8dbeb40b8
LABEL org.opencontainers.image.title="indiehr-dumper"
# Alpine 3.23's pending fixes first (as deploy/docker/postgres.Dockerfile), then Python
RUN apk upgrade --no-cache \
 && apk add --no-cache python3 py3-cryptography py3-boto3 py3-psycopg \
 && rm -f /usr/local/bin/gosu \
 && install -d -o postgres -g postgres -m 700 /state
USER postgres
WORKDIR /state
RUN python3 -c "import boto3, cryptography, psycopg" && pg_dump --version
ENTRYPOINT ["python3", "-I", "/opt/ihr-dumps/dumper.py"]
CMD ["run"]
