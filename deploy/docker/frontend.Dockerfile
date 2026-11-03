# IndieHR web frontend image (P1-T12). Hardened replacement for apps/frontend/Dockerfile:
# - runs as a non-root user (nginx-unprivileged, port 8080)
# - serves a pre-built bundle: build first with `npm ci && npm run build` in apps/frontend
# Build: docker build -f deploy/docker/frontend.Dockerfile -t indiehr-frontend apps/frontend
FROM nginxinc/nginx-unprivileged:1.30.6-alpine

COPY --chown=nginx:nginx build /usr/share/nginx/html
COPY --from=config frontend-nginx.conf /etc/nginx/conf.d/default.conf

USER nginx
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1:8080/index.html >/dev/null || exit 1
