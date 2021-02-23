ARG ARCH
FROM ${ARCH}python:3.8-slim

# set version label
ARG BUILD_DATE
ARG VERSION
LABEL build_version="Apprise API version:- ${VERSION} Build-date:- ${BUILD_DATE}"
LABEL maintainer="Chris-Caron"

# set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV APPRISE_CONFIG_DIR /config

# Install nginx and supervisord
RUN apt-get update && \
    apt-get install -y -qq nginx supervisor build-essential libffi-dev libssl-dev python-dev rustc

# Install requirements and gunicorn
COPY ./requirements.txt /etc/requirements.txt
RUN pip3 install -qq -r /etc/requirements.txt gunicorn

# Nginx configuration
RUN echo "daemon off;" >> /etc/nginx/nginx.conf
COPY /etc/nginx.conf /etc/nginx/conf.d/nginx.conf

# Copy our static content in place
COPY apprise_api/static /usr/share/nginx/html/s/

# Supervisor configuration
COPY /etc/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# set work directory
WORKDIR /opt/apprise

# Copy over Apprise API
COPY apprise_api/ webapp

# Change port of gunicorn
RUN sed -i -e 's/:8000/:8080/g' /opt/apprise/webapp/gunicorn.conf.py

# Cleanup
RUN apt-get remove -y -qq build-essential libffi-dev libssl-dev python-dev rustc && \
    apt-get clean autoclean && \
    apt-get autoremove --yes && \
    rm -rf /var/lib/{apt,dpkg,cache,log}/

EXPOSE 8000
VOLUME /config

CMD ["/usr/bin/supervisord"]
