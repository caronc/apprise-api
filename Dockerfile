FROM python:3.11-slim AS base

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

FROM base AS builder

WORKDIR /build/

# Install nginx, supervisord, and cryptography dependencies
RUN set -eux && \
    echo "Installing build dependencies" && \
        apt-get update -qq && \
        apt-get install -y -qq \
            curl \
            build-essential \
            libffi-dev \
            libssl-dev \
            pkg-config && \
    echo "Updating pip and getting requirements to build" && \
        # Cryptography documents that the latest version of pip3 must always be used
        python3 -m pip install --upgrade \
            pip \
            wheel && \
    echo "Installing latest rustc" && \
        # Pull in bleeding edge of rust to keep up with cryptography build requirements
        curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal && \
        . "$HOME/.cargo/env" && \
    echo "Buildingcryptography" && \
        python3 -m pip wheel \
            --no-binary cryptography \
            cryptography

FROM base AS runtime

# Install requirements and gunicorn
COPY ./requirements.txt /etc/requirements.txt
COPY --from=builder /build/*.whl ./
RUN set -eux && \
    echo "Installing cryptography" && \
        pip3 install *.whl && \
    echo "Installing python requirements" && \
        pip3 install --no-cache-dir -q -r /etc/requirements.txt gunicorn supervisor && \
    echo "Installing nginx" && \
        apt-get update -qq && \
        apt-get install -y -qq \
            nginx && \
    echo "Installing tools" && \
        apt-get install -y -qq \
            sed && \
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

# Configuration Permissions (to run nginx as a non-root user)
RUN umask 0002 && \
    touch /etc/nginx/server-override.conf && \
    touch /etc/nginx/location-override.conf

VOLUME /config
VOLUME /attach
VOLUME /plugin
EXPOSE 8000
CMD ["/opt/apprise/webapp/supervisord-startup"]
