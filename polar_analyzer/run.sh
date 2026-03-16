#!/usr/bin/with-contenv bashio
# run.sh — Home Assistant addon entry point for Polar Analyzer.
# Reads HA addon options and maps them to POLAR_ANALYZER_* env vars.

set -e

# Read addon options
SIGNALK_URL=$(bashio::config 'signalk_url')
SIGNALK_HTTP_URL=$(bashio::config 'signalk_http_url')
LOG_LEVEL=$(bashio::config 'log_level')

# Map to application env vars
export POLAR_ANALYZER_SIGNALK_URL="${SIGNALK_URL}"
export POLAR_ANALYZER_SIGNALK_HTTP_URL="${SIGNALK_HTTP_URL}"
export POLAR_ANALYZER_TOKEN_FILE="/data/signalk_token.json"
export POLAR_ANALYZER_DATA_DIR="/data"
export POLAR_ANALYZER_WEB_STATIC_DIR="/app/web"
export POLAR_ANALYZER_WEB_PORT="3001"

# Map log level to verbose flag
VERBOSE_FLAG=""
if [ "${LOG_LEVEL}" = "debug" ]; then
    VERBOSE_FLAG="-v"
fi

bashio::log.info "Starting Polar Analyzer..."
bashio::log.info "SignalK URL: ${SIGNALK_URL}"
bashio::log.info "Data dir: /data"

# exec ensures SIGTERM propagates to the Python process for graceful shutdown
exec python3 /app/src/main.py live ${VERBOSE_FLAG}
