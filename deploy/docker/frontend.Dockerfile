# IndieHR web frontend image (P1-T12). Hardened replacement for apps/frontend/Dockerfile:
# - runs as a non-root user (nginx-unprivileged, port 8080)
# - serves a pre-built bundle: build first with `npm ci && npm run build` in apps/frontend
# Build (context is the built bundle, so apps/frontend/.dockerignore does not apply):
#   docker buildx build -f deploy/docker/frontend.Dockerfile --build-context config=deploy/docker \
#     -t indiehr-frontend apps/frontend/build
FROM nginxinc/nginx-unprivileged:1.30.6-alpine

COPY --chown=nginx:nginx . /usr/share/nginx/html
COPY --from=config frontend-nginx.conf.template /etc/nginx/templates/default.conf.template
# Allowed API/storage origins for the CSP; set per deployment at `docker run -e ...`.
ENV CSP_API_ORIGIN="" CSP_STORAGE_ORIGIN=""
# Same-origin API proxy (see the template); off unless API_UPSTREAM is set.
ENV API_UPSTREAM="" NGINX_RESOLVER="127.0.0.11"

USER nginx
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1:8080/index.html >/dev/null || exit 1
