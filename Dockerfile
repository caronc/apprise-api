FROM python:3.12-slim AS base

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

FROM base AS runtime

# Install requirements and gunicorn
COPY ./requirements.txt /etc/requirements.txt

RUN set -eux && \
    echo "Installing nginx" && \
        apt-get update -qq && \
        apt-get install -y -qq \
            nginx && \
    echo "Installing tools" && \
        apt-get install -y -qq \
            curl sed git && \
    echo "Installing python requirements" && \
        pip3 install --no-cache-dir -q -r /etc/requirements.txt gunicorn supervisor && \
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

# Configuration Permissions (to run nginx as a non-root user)
RUN umask 0002 && \
    touch /etc/nginx/server-override.conf && \
    touch /etc/nginx/location-override.conf && \
    mkdir -p /config /attach /plugin

VOLUME /config
VOLUME /attach
VOLUME /plugin
EXPOSE 8000
CMD ["/opt/apprise/webapp/supervisord-startup"]
