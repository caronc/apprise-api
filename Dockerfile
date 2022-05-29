ARG ARCH
FROM ${ARCH}python:3.10-slim

# set version label
ARG BUILD_DATE
ARG VERSION
LABEL build_version="Apprise API version:- ${VERSION} Build-date:- ${BUILD_DATE}"
LABEL maintainer="Chris-Caron"

# set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV APPRISE_CONFIG_DIR /config
ENV CRYPTOGRAPHY_DONT_BUILD_RUST=1

# Install nginx and supervisord
RUN apt-get update -qq && \
    apt-get install -y -qq nginx supervisor build-essential libffi-dev libssl-dev python-dev

# Install requirements and gunicorn
COPY ./requirements.txt /etc/requirements.txt
RUN pip3 install -q -r /etc/requirements.txt gunicorn

# Copy our static content in place
COPY apprise_api/static /usr/share/nginx/html/s/

# set work directory
WORKDIR /opt/apprise

# Copy over Apprise API
COPY apprise_api/ webapp

# Cleanup
RUN apt-get remove -y -qq build-essential libffi-dev libssl-dev python-dev && \
    apt-get clean autoclean && \
    apt-get autoremove --yes && \
    rm -rf /var/lib/{apt,dpkg,cache,log}/

# Configuration Permissions (to run nginx as a non-root user)
RUN umask 0002 && \
    mkdir -p /config /run/apprise && \
    chown www-data:www-data -R /run/apprise /var/lib/nginx /config

# Handle running as a non-root user (www-data is id/gid 33)
USER www-data
VOLUME /config
EXPOSE 8000
CMD ["/usr/bin/supervisord", "-c", "/opt/apprise/webapp/etc/supervisord.conf"]
