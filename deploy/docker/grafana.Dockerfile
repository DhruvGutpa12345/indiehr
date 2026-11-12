# Grafana for the operator's dashboards (P1-T9d, ADR-015; owner decisions pending Q-O13, Q-O14).
# Grafana OSS 13.3.1, unmodified (AGPL-3.0: used as it is, reached over the network only;
# CLAUDE.md), with the VictoriaLogs datasource plugin v0.34.0 (Apache-2.0, signed by its vendor)
# placed beside it at build time, checked by its SHA-256. Nothing is downloaded at run time, and
# every setting that would call out (usage reports, update checks, plugin preinstall and updates,
# news, gravatar, external snapshots, the plugin catalog) is off here, so no deployment can forget
# one, and grafana.com itself points at a dead end on this host (127.0.0.1:9), so a caller we
# missed fails here instead of reaching out. Unused bundled datasources are removed and switched off (only Prometheus and the logs
# plugin are there); a strict Content-Security-Policy and short sessions are on. Grafana is a viewer only: alerting stays with Prometheus, Alertmanager and vmalert. Build:
#   docker buildx build -f deploy/docker/grafana.Dockerfile -t <tag> deploy/docker
ARG GRAFANA_IMAGE=grafana/grafana:13.3.1@sha256:0615ec4fbd4072004b7d86b46ded63013a74b7d6eb1119273dd89e8fa25c13cf

FROM ${GRAFANA_IMAGE} AS plugin
USER root
ADD --checksum=sha256:6fc61446097a525e826f2ddb6746723f85447845c95a7b1a99624e84d4b2e237 \
    https://github.com/VictoriaMetrics/victorialogs-datasource/releases/download/v0.34.0/victoriametrics-logs-datasource-v0.34.0.zip \
    /tmp/victorialogs.zip
RUN mkdir -p /opt/ihr-grafana-plugins \
 && unzip -q /tmp/victorialogs.zip -d /opt/ihr-grafana-plugins \
 && test -f /opt/ihr-grafana-plugins/victoriametrics-logs-datasource/plugin.json \
 && cd /opt/ihr-grafana-plugins/victoriametrics-logs-datasource \
 && test -f victoriametrics_logs_backend_plugin_linux_amd64 \
 && chmod -R a+rX /opt/ihr-grafana-plugins \
 && chmod a+x victoriametrics_logs_backend_plugin_linux_amd64
# Every file stays: the vendor's signed MANIFEST lists all of them (other platforms included),
# and Grafana refuses a plugin with one missing.

FROM ${GRAFANA_IMAGE}
LABEL org.opencontainers.image.title="indiehr-grafana" \
      ihr.grafana.version="13.3.1" \
      ihr.victorialogs-datasource.version="0.34.0"
COPY --from=plugin /opt/ihr-grafana-plugins /opt/ihr-grafana-plugins
# Only the Prometheus datasource and the canvas panel of the bundled plugins are used; the others
# (and their findings in Trivy) leave the image. Grafana's own code is not touched.
USER root
RUN cd /usr/share/grafana/data/plugins-bundled \
 && for p in *; do case "$p" in prometheus|canvas) ;; *) rm -rf -- "$p" ;; esac; done \
 && test -d prometheus && test "$(ls | wc -l)" = 2
ENV GF_PATHS_PLUGINS=/opt/ihr-grafana-plugins \
    GF_PATHS_LOGS=/tmp \
    GF_LOG_MODE=console \
    GF_ANALYTICS_REPORTING_ENABLED=false \
    GF_ANALYTICS_CHECK_FOR_UPDATES=false \
    GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false \
    GF_ANALYTICS_FEEDBACK_LINKS_ENABLED=false \
    GF_NEWS_NEWS_FEED_ENABLED=false \
    GF_SECURITY_DISABLE_GRAVATAR=true \
    GF_SNAPSHOTS_ENABLED=false \
    GF_SNAPSHOTS_EXTERNAL_ENABLED=false \
    GF_PLUGINS_PREINSTALL_DISABLED=true \
    GF_PLUGINS_PREINSTALL_AUTO_UPDATE=false \
    GF_PLUGINS_PLUGIN_ADMIN_ENABLED=false \
    GF_PLUGINS_PUBLIC_KEY_RETRIEVAL_DISABLED=true \
    GF_UNIFIED_ALERTING_ENABLED=false \
    GF_AUTH_ANONYMOUS_ENABLED=false \
    GF_USERS_ALLOW_SIGN_UP=false \
    GF_HELP_ENABLED=false \
    GF_METRICS_ENABLED=false \
    GF_PUBLIC_DASHBOARDS_ENABLED=false \
    GF_GRAFANA_COM_URL=http://127.0.0.1:9 \
    GF_GRAFANA_COM_API_URL=http://127.0.0.1:9/api \
    GF_SECURITY_DATA_SOURCE_PROXY_WHITELIST="prometheus:9090 logs-reader:8427" \
    GF_SECURITY_X_XSS_PROTECTION=false \
    GF_PLUGINS_DISABLE_PLUGINS=alertmanager,cloudwatch,elasticsearch,grafana-azure-monitor-datasource,grafana-postgresql-datasource,grafana-pyroscope-datasource,grafana-testdata-datasource,graphite,influxdb,jaeger,loki,mssql,mysql,opentsdb,stackdriver,tempo,zipkin \
    GF_SECURITY_CONTENT_SECURITY_POLICY=true \
    GF_SECURITY_CONTENT_SECURITY_POLICY_TEMPLATE="script-src 'self' 'unsafe-eval' 'unsafe-inline' 'strict-dynamic' \$NONCE;object-src 'none';font-src 'self';style-src 'self' 'unsafe-inline' blob:;img-src 'self' data: blob:;base-uri 'self';connect-src 'self' ws://\$ROOT_PATH;manifest-src 'self';media-src 'none';form-action 'self';frame-ancestors 'none';" \
    GF_AUTH_LOGIN_MAXIMUM_INACTIVE_LIFETIME_DURATION=1h \
    GF_AUTH_LOGIN_MAXIMUM_LIFETIME_DURATION=12h
USER 472
