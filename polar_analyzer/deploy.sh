#!/bin/bash
# deploy.sh — Build and deploy the Polar Analyzer HA addon.
#
# Usage:
#   ./deploy.sh              Build locally
#   ./deploy.sh push         Build and push to registry
#
# This script stages src/ and web/ into the addon build context
# (Docker cannot COPY from outside the context), builds the image,
# then cleans up the staged files.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUILD_DIR="${SCRIPT_DIR}"

IMAGE_NAME="polar-analyzer"
IMAGE_TAG="latest"

echo "=== Polar Analyzer HA Addon Build ==="
echo "Project root: ${PROJECT_ROOT}"
echo "Build dir:    ${BUILD_DIR}"

# Stage source files into build context
echo "Staging source files..."
cp -r "${PROJECT_ROOT}/src" "${BUILD_DIR}/src"
cp -r "${PROJECT_ROOT}/web" "${BUILD_DIR}/web"

cleanup() {
    echo "Cleaning up staged files..."
    rm -rf "${BUILD_DIR}/src" "${BUILD_DIR}/web"
}
trap cleanup EXIT

# Build
echo "Building Docker image..."
docker build \
    --build-arg BUILD_FROM="ghcr.io/home-assistant/aarch64-base-python:3.11-alpine3.18" \
    -t "${IMAGE_NAME}:${IMAGE_TAG}" \
    "${BUILD_DIR}"

echo "Build complete: ${IMAGE_NAME}:${IMAGE_TAG}"

# Optional push
if [ "${1:-}" = "push" ]; then
    echo "Pushing to registry..."
    docker push "${IMAGE_NAME}:${IMAGE_TAG}"
    echo "Push complete."
fi
