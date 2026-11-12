#!/bin/sh
# Our SeaweedFS in one container (P1-T8b3, ADR-011): storage (`weed mini`: master, volume server,
# filer) on 127.0.0.1 only, and the S3 gateway (`weed s3`), the only API other containers reach.
#
# Why loopback: the filer's own HTTP and gRPC APIs have no access control and sit underneath S3,
# so anything that reached them could delete a COMPLIANCE-locked bucket outright (tried on 4.53:
# one recursive DELETE removed it). Why one container: a gateway in another container's network
# namespace is left behind in a dead namespace when that container restarts; here, if either
# process stops, the container stops and Docker's restart policy brings both back together.
#
# Keys come from the environment (env_file), are checked, written to /tmp/s3.json (tmpfs,
# mode 600) and removed from the environment before either process starts.
# Disk: at most IHR_S3_MAX_GB (default 20) GB of volumes of IHR_S3_VOLUME_MB (default 256) MB.
set -eu
umask 077
TEMPLATE=/etc/seaweedfs/s3.template.json
CONFIG=/tmp/s3.json
KEYS="IHR_S3_ADMIN_KEY IHR_S3_ADMIN_SECRET IHR_S3_CARE_KEY IHR_S3_CARE_SECRET
IHR_S3_ANCHOR_WRITE_KEY IHR_S3_ANCHOR_WRITE_SECRET IHR_S3_ANCHOR_READ_KEY IHR_S3_ANCHOR_READ_SECRET
IHR_S3_BACKUP_KEY IHR_S3_BACKUP_SECRET IHR_S3_LOGS_ARCHIVE_KEY IHR_S3_LOGS_ARCHIVE_SECRET"
BUCKETS="IHR_S3_PATIENT_BUCKET IHR_S3_FACILITY_BUCKET IHR_S3_ANCHOR_BUCKET IHR_S3_BACKUP_BUCKET IHR_S3_LOGS_ARCHIVE_BUCKET"
x=""
for v in $KEYS; do
  eval "x=\${$v:-}"
  case "$x" in
    ""|*[!A-Za-z0-9]*) echo "s3: $v missing or not letters and digits only" >&2; exit 1 ;;
  esac
  [ "${#x}" -ge 16 ] || { echo "s3: $v shorter than 16 characters" >&2; exit 1; }
done
for v in $BUCKETS; do
  eval "x=\${$v:-}"
  case "$x" in
    ""|*[!a-z0-9-]*) echo "s3: $v missing or not a bucket name (a-z, 0-9, -)" >&2; exit 1 ;;
  esac
done
MAX_GB="${IHR_S3_MAX_GB:-20}"
VOLUME_MB="${IHR_S3_VOLUME_MB:-256}"
case "$MAX_GB$VOLUME_MB" in *[!0-9]*) echo "s3: IHR_S3_MAX_GB / IHR_S3_VOLUME_MB must be numbers" >&2; exit 1 ;; esac
VOLUMES=$((MAX_GB * 1024 / VOLUME_MB))
[ "$VOLUMES" -ge 8 ] || { echo "s3: IHR_S3_MAX_GB too small for $VOLUME_MB MB volumes (need 8 or more)" >&2; exit 1; }

sed -e "s|@ADMIN_KEY@|$IHR_S3_ADMIN_KEY|g" -e "s|@ADMIN_SECRET@|$IHR_S3_ADMIN_SECRET|g" \
    -e "s|@CARE_KEY@|$IHR_S3_CARE_KEY|g" -e "s|@CARE_SECRET@|$IHR_S3_CARE_SECRET|g" \
    -e "s|@ANCHOR_WRITE_KEY@|$IHR_S3_ANCHOR_WRITE_KEY|g" -e "s|@ANCHOR_WRITE_SECRET@|$IHR_S3_ANCHOR_WRITE_SECRET|g" \
    -e "s|@ANCHOR_READ_KEY@|$IHR_S3_ANCHOR_READ_KEY|g" -e "s|@ANCHOR_READ_SECRET@|$IHR_S3_ANCHOR_READ_SECRET|g" \
    -e "s|@PATIENT_BUCKET@|$IHR_S3_PATIENT_BUCKET|g" -e "s|@FACILITY_BUCKET@|$IHR_S3_FACILITY_BUCKET|g" \
    -e "s|@BACKUP_KEY@|$IHR_S3_BACKUP_KEY|g" -e "s|@BACKUP_SECRET@|$IHR_S3_BACKUP_SECRET|g" \
    -e "s|@BACKUP_BUCKET@|$IHR_S3_BACKUP_BUCKET|g" \
    -e "s|@LOGS_ARCHIVE_KEY@|$IHR_S3_LOGS_ARCHIVE_KEY|g" -e "s|@LOGS_ARCHIVE_SECRET@|$IHR_S3_LOGS_ARCHIVE_SECRET|g" \
    -e "s|@LOGS_ARCHIVE_BUCKET@|$IHR_S3_LOGS_ARCHIVE_BUCKET|g" \
    -e "s|@ANCHOR_BUCKET@|$IHR_S3_ANCHOR_BUCKET|g" "$TEMPLATE" > "$CONFIG"
if grep -q '@[A-Z_]*@' "$CONFIG"; then echo "s3: placeholder left in $CONFIG" >&2; exit 1; fi
# shellcheck disable=SC2086  # word lists on purpose
unset $KEYS

# -logtostderr: logs go to Docker (the root file system is read-only)
/usr/bin/weed -logtostderr mini -dir=/data -ip=127.0.0.1 -ip.bind=127.0.0.1 -s3=false \
  -webdav=false -admin.ui=false -master.volumeSizeLimitMB="$VOLUME_MB" -volume.max="$VOLUMES" &
STORAGE=$!
up() { wget -q -O /dev/null "http://127.0.0.1:$1" 2>/dev/null; }
i=0
until up 9333/cluster/status && up 9340/status && up 8888/healthz; do
  i=$((i + 1))
  if [ "$i" -gt 120 ] || ! kill -0 "$STORAGE" 2>/dev/null; then echo "s3: storage did not start" >&2; exit 1; fi
  sleep 1
done

# -metricsPort: the gateway's request counts and latencies for Prometheus (P1-T9a), read only
# over the monitoring network (compose.s3.yaml); counters, no object names or keys
set -- -filer=127.0.0.1:8888 -config="$CONFIG" -ip.bind=0.0.0.0 -port=8333 \
  -port.iceberg=0 -port.lance=0 -iam=false -metricsPort=9327
# Behind Traefik (staging): the public address presigned URLs are signed for
[ -z "${IHR_S3_EXTERNAL_URL:-}" ] || set -- "$@" -externalUrl="$IHR_S3_EXTERNAL_URL"
/usr/bin/weed -logtostderr s3 "$@" &
GATEWAY=$!

stop() { kill -TERM "$GATEWAY" "$STORAGE" 2>/dev/null || true; wait 2>/dev/null || true; }
trap 'stop; exit 0' TERM INT
# Both or neither: when one process ends, stop the other and exit non-zero (Docker restarts us)
while kill -0 "$STORAGE" 2>/dev/null && kill -0 "$GATEWAY" 2>/dev/null; do sleep 2; done
echo "s3: $(kill -0 "$STORAGE" 2>/dev/null && echo gateway || echo storage) stopped; stopping the container" >&2
stop
exit 1
