# Keycloak 26.8.2 pre-built for the staging stack (P1-T8d1). Keycloak's own "optimized" image
# pattern (keycloak.org/server/containers): the build-time options are fixed here once, and the
# server starts with `start --optimized` instead of re-checking (and possibly re-building) on
# every start. Runtime options (hostname, proxy, cache, database address and password, event
# levels) stay in compose.yaml. With --optimized a runtime value that differs from a build-time
# one below makes Keycloak refuse to start, so they live only here. Build:
#   docker buildx build -f deploy/docker/keycloak.Dockerfile -t <tag> deploy/docker
ARG KEYCLOAK_IMAGE=quay.io/keycloak/keycloak:26.8.2@sha256:723b1d6e254c0fc96d5cffec860c7e0744d6a3277822b865bb538cf891575e41

FROM ${KEYCLOAK_IMAGE} AS build
# Postgres only; health and metrics on the management port 9000 (never published or routed:
# metrics add the database check to /health/ready); features nobody here uses stay off
ENV KC_DB=postgres \
    KC_HEALTH_ENABLED=true \
    KC_METRICS_ENABLED=true \
    KC_FEATURES_DISABLED=device-flow,ciba,impersonation,kerberos
RUN /opt/keycloak/bin/kc.sh build && /opt/keycloak/bin/kc.sh show-config | grep -E 'kc\.(db|health-enabled|metrics-enabled|features-disabled) '

FROM ${KEYCLOAK_IMAGE}
LABEL org.opencontainers.image.title="indiehr-keycloak"
COPY --from=build /opt/keycloak/ /opt/keycloak/
USER 1000
ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]
CMD ["start", "--optimized"]
