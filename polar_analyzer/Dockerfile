ARG BUILD_FROM
FROM $BUILD_FROM

# Install Python and build tools for native extensions (numpy, scipy)
RUN apk add --no-cache \
        python3 \
        py3-pip \
        py3-numpy \
        py3-scipy

# Install Python dependencies that aren't available as apk packages
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Copy application code
COPY src /app/src
COPY web /app/web

# Bare imports in src/ — Python needs src/ on the path
ENV PYTHONPATH="/app/src"

# Copy run script
COPY run.sh /app/run.sh
RUN chmod +x /app/run.sh

# Create data directory
RUN mkdir -p /data

WORKDIR /app
CMD ["/app/run.sh"]
