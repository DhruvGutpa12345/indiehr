# acme-dns for the wildcard certificate (P1-T8b2, ADR-011). Upstream's own image is built
# FROM alpine:latest and runs as root, so we build it ourselves: source pinned to one commit
# and checked by SHA-256, Go and the base image pinned by digest, a static binary, no shell,
# not root. Build: docker buildx build -f deploy/docker/acme-dns.Dockerfile -t <tag> deploy/docker
#
# Commit 43b0aca (08-06-2026) is the v2.0.2 release plus upstream's later fixes that bound the
# DNS read path and index the TXT table (both unreleased so far).
ARG ACMEDNS_COMMIT=43b0acaacef24d1058d70ed584d6b7275a8344ed

FROM golang:1.27.3-alpine3.23@sha256:5d5113301ffcac84e2bfb20c42648803fecaea6789c89bb9b66884e18cbb30a7 AS build
ARG ACMEDNS_COMMIT
ADD --checksum=sha256:7faa984517d6f0a0c356532519a9eb4b152f8614daa35a1d44c851d667da7206 \
    https://github.com/acme-dns/acme-dns/archive/${ACMEDNS_COMMIT}.tar.gz /src.tar.gz
WORKDIR /src
# Modules are checked against upstream's go.sum (GOFLAGS=-mod=readonly is Go's default);
# GOTOOLCHAIN=local: never download another Go toolchain during the build. Upstream's tests run
# against our module versions; one is skipped because it needs a non-root user (builds run as root).
ENV CGO_ENABLED=0 GOTOOLCHAIN=local GOFLAGS=-trimpath
COPY acme-dns/go.mod acme-dns/go.sum /overlay/
RUN tar -xzf /src.tar.gz --strip-components=1 \
 && cp /overlay/go.mod /overlay/go.sum . \
 && go mod download \
 && go mod verify \
 && go test -skip '^TestFileCheckPermissionDenied$' ./... \
 && go build -ldflags="-s -w -buildid=" -o /out/acme-dns . \
 && mkdir -p /out/data

FROM gcr.io/distroless/static-debian13:nonroot@sha256:25ac8f0c3b2334a9eb05452266bd4697864a8c8ef0ef71e5e6480d7f92868329
ARG ACMEDNS_COMMIT
LABEL org.opencontainers.image.title="indiehr-acme-dns" \
      org.opencontainers.image.source="https://github.com/acme-dns/acme-dns" \
      org.opencontainers.image.licenses="MIT" \
      ihr.acme-dns.commit="${ACMEDNS_COMMIT}"
COPY --from=build /out/acme-dns /usr/local/bin/acme-dns
# MIT: the licence travels with the binary
COPY --from=build /src/LICENSE /usr/share/doc/acme-dns/LICENSE
# The database folder, owned by the nonroot user; a new named volume copies this ownership
COPY --from=build --chown=65532:65532 /out/data /var/lib/acme-dns
USER 65532:65532
# DNS on 53 inside the container (Docker lets unprivileged processes bind low ports in a
# container's own network namespace); the HTTP API only on the internal network
EXPOSE 53/udp 53/tcp 8053/tcp
ENTRYPOINT ["/usr/local/bin/acme-dns"]
CMD ["-c", "/etc/acme-dns/config.cfg"]
