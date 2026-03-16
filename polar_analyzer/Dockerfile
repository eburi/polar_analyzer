ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-python:3.11-alpine3.18
FROM ${BUILD_FROM}

# Install build dependencies for numpy/scipy
RUN apk add --no-cache \
    gcc \
    g++ \
    musl-dev \
    gfortran \
    openblas-dev \
    py3-numpy \
    py3-scipy

# Copy requirements and install Python deps
COPY requirements.txt /tmp/
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Copy application code (staged by deploy.sh into build context)
COPY src /app/src
COPY web /app/web

# Bare imports in src/ — Python needs src/ on the path
ENV PYTHONPATH="/app/src"

# Copy run script
COPY run.sh /
RUN chmod a+x /run.sh

# Create data directory
RUN mkdir -p /data

WORKDIR /app

CMD [ "/run.sh" ]
