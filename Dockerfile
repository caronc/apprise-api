# Use the standard Nginx image from Docker Hub
FROM nginx

# set version label
ARG BUILD_DATE
ARG VERSION
ARG HEALTHCHECKS_RELEASE
LABEL build_version="Apprise API version:- ${VERSION} Build-date:- ${BUILD_DATE}"
LABEL maintainer="Chris-Caron"

# set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV APPRISE_CONFIG_DIR /config

# Install Python Dependencies
COPY ./requirements.txt etc/requirements.txt

# Install Python
RUN apt-get update && \
    apt-get install -y curl python3 python3-pip && \
    pip3 install -r etc/requirements.txt gunicorn

# Install s6-overlay
RUN curl -fL -o /tmp/s6-overlay.tar.gz \
         https://github.com/just-containers/s6-overlay/releases/download/v1.22.1.0/s6-overlay-amd64.tar.gz \
 && tar -xzf /tmp/s6-overlay.tar.gz -C / \
 && rm -rf /tmp/*

ENV S6_KEEP_ENV=1 \
    S6_CMD_WAIT_FOR_SERVICES=1

# Copy our static content in place
COPY apprise_api/static /usr/share/nginx/html/s/

# System Configuration
COPY etc /etc/

# set work directory
WORKDIR /opt/apprise

# Copy over Apprise API
COPY apprise_api/ webapp

# gunicorn to expose on port 8080
# nginx to expose on port 8000
# disable logging on gunicorn
RUN \
   sed -i -e 's/backend:8000/localhost:8080/g' \
          -e 's/listen\([ \t]\+\)[^;]\+;/listen\18000;/g' \
                     /etc/nginx/conf.d/default.conf && \
   sed -i -e 's/:8000/:8080/g' /opt/apprise/webapp/gunicorn.conf.py

EXPOSE 8000
VOLUME /config

ENTRYPOINT ["/init"]
