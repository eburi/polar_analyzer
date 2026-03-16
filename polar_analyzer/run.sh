#!/usr/bin/with-contenv bashio
set -e

# HA Add-on entry point for polar_analyzer.
# Reads user configuration via bashio and launches main.py.

bashio::log.info "Reading add-on configuration..."

# Read config values via bashio (falls back to defaults from config.yaml)
SIGNALK_URL="$(bashio::config 'signalk_url')"
SIGNALK_HTTP_URL="$(bashio::config 'signalk_http_url')"
LOG_LEVEL="$(bashio::config 'log_level')"

bashio::log.info "Signal K WS URL:   ${SIGNALK_URL}"
bashio::log.info "Signal K HTTP URL: ${SIGNALK_HTTP_URL}"
bashio::log.info "Log level:         ${LOG_LEVEL}"

# Export as environment variables for main.py to read via Config.from_env()
export POLAR_ANALYZER_SIGNALK_URL="${SIGNALK_URL}"
export POLAR_ANALYZER_SIGNALK_HTTP_URL="${SIGNALK_HTTP_URL}"
export POLAR_ANALYZER_TOKEN_FILE="/data/signalk_token.json"
export POLAR_ANALYZER_DATA_DIR="/data"
export POLAR_ANALYZER_WEB_STATIC_DIR="/app/web"
export POLAR_ANALYZER_WEB_PORT="3001"

# Build CLI args
ARGS="live"
if [ "${LOG_LEVEL}" = "debug" ]; then
    ARGS="${ARGS} -v"
fi

bashio::log.info "Starting: python3 /app/src/main.py ${ARGS}"

# exec ensures SIGTERM propagates to the Python process for graceful shutdown
exec python3 /app/src/main.py ${ARGS}
