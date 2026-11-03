# IndieHR backend production image (P0-T12).
# Based on upstream CARE apps/backend/docker/prod.Dockerfile (MIT), hardened:
# - base image pinned to an exact Python patch release
# - pip removed from the final image (system + venv): it is not needed at runtime and
#   carries its own outdated vendored copies of certifi/urllib3/msgpack/setuptools
# - runs as the non-root user "django" (as upstream)
# Build (context = the CARE backend):
#   docker buildx build -f deploy/docker/backend.Dockerfile -t indiehr-backend apps/backend
FROM python:3.13.18-slim-bookworm AS base

ARG APP_HOME=/app
ARG BUILD_ENVIRONMENT="production"

WORKDIR $APP_HOME

ENV BUILD_ENVIRONMENT=$BUILD_ENVIRONMENT
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIPENV_VENV_IN_PROJECT=1
ENV PIPENV_CACHE_DIR=/root/.cache/pip
ENV PATH=$APP_HOME/.venv/bin:$PATH
ENV HOME=$APP_HOME


# ---
FROM base AS builder

RUN apt-get update && apt-get install --no-install-recommends -y \
  build-essential libjpeg-dev zlib1g-dev libgmp-dev libpq-dev git wget \
  libpango-1.0-0 libharfbuzz0b libpangoft2-1.0-0 libharfbuzz-subset0 libffi-dev libopenjp2-7-dev \
  && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
  && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pipenv==2025.1.1

RUN python -m venv $APP_HOME/.venv
COPY Pipfile Pipfile.lock $APP_HOME/
RUN pipenv install --deploy --categories "packages"

COPY plugs/ $APP_HOME/plugs/
COPY install_plugins.py plug_config.py $APP_HOME/

ARG ADDITIONAL_PLUGS=""
ENV ADDITIONAL_PLUGS=$ADDITIONAL_PLUGS
RUN python3 $APP_HOME/install_plugins.py

# Plugins are installed; the venv no longer needs pip.
RUN $APP_HOME/.venv/bin/python -m pip uninstall -y pip


# ---
FROM base AS runtime

RUN addgroup --system django \
  && adduser --system --ingroup django django

RUN apt-get update && apt-get install --no-install-recommends -y \
  libpq-dev libgmp-dev libpangoft2-1.0-0 gettext wget curl gnupg \
  && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
  && rm -rf /var/lib/apt/lists/* \
  && python -m pip uninstall -y pip

RUN chown django:django $APP_HOME

COPY --from=builder --chown=django:django $APP_HOME/.venv $APP_HOME/.venv

ARG APP_VERSION="unknown"
ENV APP_VERSION=$APP_VERSION

COPY --chmod=0755 --chown=django:django ./scripts/*.sh $APP_HOME

COPY --chown=django:django . $APP_HOME

USER django

HEALTHCHECK \
  --interval=30s \
  --timeout=5s \
  --start-period=10s \
  --retries=12 \
  CMD ["./healthcheck.sh"]

EXPOSE 9000
