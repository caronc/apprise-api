FROM python:3.14-slim AS base

# set version label
ARG BUILD_DATE
ARG VERSION
LABEL build_version="Apprise API version:- ${VERSION} Build-date:- ${BUILD_DATE}"
LABEL maintainer="Chris-Caron"

# set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV APPRISE_CONFIG_DIR=/config
ENV APPRISE_ATTACH_DIR=/attach
ENV APPRISE_PLUGIN_PATHS=/plugin
ENV TZ=Etc/UTC

FROM base AS builder

# python-qpid-proton (AMQP 1.0) is installed here rather than through
# requirements.txt because it must be compiled on Linux
COPY ./requirements.txt /etc/requirements.txt

RUN set -eux && \
    echo "Installing build tools" && \
        apt-get update -qq && \
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
            build-essential pkg-config libssl-dev git && \
    echo "Building python wheels" && \
        pip3 wheel --no-cache-dir -q --wheel-dir /wheels \
            -r /etc/requirements.txt gunicorn supervisor \
            python-qpid-proton

FROM base AS runtime

# Install requirements and gunicorn
COPY ./requirements.txt /etc/requirements.txt

# The wheels from the builder are only mounted while installing, so they
# do not take up space in the final image
RUN --mount=type=bind,from=builder,source=/wheels,target=/wheels \
    set -eux && \
    echo "Installing nginx" && \
        apt-get update -qq && \
        DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq && \
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
            nginx \
            tzdata && \
    echo "Installing tools" && \
        apt-get install -y -qq --no-install-recommends \
            curl sed git && \
    echo "Installing python requirements" && \
        pip3 install --no-cache-dir -q --no-index --find-links /wheels \
            -r /etc/requirements.txt gunicorn supervisor \
            python-qpid-proton && \
        pip freeze && \
    echo "Cleaning up" && \
        apt-get --yes autoremove --purge && \
        apt-get clean --yes && \
        rm --recursive --force --verbose /var/lib/apt/lists/* && \
        rm --recursive --force --verbose /tmp/* && \
        rm --recursive --force --verbose /var/tmp/* && \
        rm --recursive --force --verbose /var/cache/apt/archives/* && \
        truncate --size 0 /var/log/*log

# Copy our static content in place
COPY apprise_api/static /usr/share/nginx/html/s/

# set work directory
WORKDIR /opt/apprise

# Copy over Apprise API
COPY apprise_api/ webapp

# Compile the translations for Apprise API and Apprise; without them every
# language quietly falls back to English. polib is only needed for this step.
RUN set -eux && \
    pip3 install --no-cache-dir -q polib && \
    python3 webapp/apprise_translations.py && \
    pip3 uninstall --yes -q polib

# Directory Setup
RUN umask 0002 && \
    touch /etc/nginx/server-override.conf && \
    touch /etc/nginx/location-override.conf && \
    mkdir -p /config/store /attach /plugin /tmp/apprise && \
     chmod 1777 /tmp/apprise && \
        chmod 777 /config /config/store /attach /plugin

# Essential:
VOLUME /config

# Optional:
VOLUME /attach
VOLUME /plugin
EXPOSE 8000
CMD ["/opt/apprise/webapp/supervisord-startup"]
