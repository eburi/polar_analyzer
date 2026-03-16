#!/usr/bin/env bash
set -e

# Deploy polar_analyzer HA addon to a Home Assistant device.
#
# Usage:
#   ./local_deploy.sh [user@host]
#
# Default target: root@primrose.local (Primrose HA device)
#
# This script:
# 1. Assembles a self-contained addon directory in /tmp/polar_analyzer_addon/
# 2. Cleans and copies it to /addons/polar_analyzer/ on the HA device via scp
# 3. Prints instructions for installing/rebuilding in HA

TARGET="${1:-root@primrose.local}"
ADDON_NAME="polar_analyzer"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
ADDON_DIR="$PROJECT_DIR/polar_analyzer"
BUILD_DIR="/tmp/${ADDON_NAME}_addon"

echo "=== Deploying $ADDON_NAME to $TARGET ==="
echo "Project dir: $PROJECT_DIR"
echo ""

# 1. Assemble the addon directory
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

# Copy HA addon files, stripping the 'image:' line so HA builds locally
sed '/^image:/d' "$ADDON_DIR/config.yaml" > "$BUILD_DIR/config.yaml"
cp "$ADDON_DIR/Dockerfile" "$BUILD_DIR/"
cp "$ADDON_DIR/requirements.txt" "$BUILD_DIR/"
cp "$ADDON_DIR/run.sh" "$BUILD_DIR/"

# Copy source code (excluding __pycache__)
cp -r "$PROJECT_DIR/src" "$BUILD_DIR/src"
find "$BUILD_DIR/src" -type d -name '__pycache__' -exec rm -rf {} +

# Copy web assets
cp -r "$PROJECT_DIR/web" "$BUILD_DIR/web"

echo "Assembled addon in $BUILD_DIR:"
ls -la "$BUILD_DIR/"
echo ""
echo "Source files:"
ls -la "$BUILD_DIR/src/"
echo ""

# 2. Clean and copy to HA device
echo "Cleaning and copying to $TARGET:/addons/$ADDON_NAME/ ..."
ssh "$TARGET" "rm -rf /addons/$ADDON_NAME && mkdir -p /addons/$ADDON_NAME"
scp -r "$BUILD_DIR/"* "$TARGET:/addons/$ADDON_NAME/"

echo ""
echo "=== Deploy complete ==="
echo ""
echo "Next steps on Home Assistant:"
echo "  1. Go to Settings → Apps → App Store"
echo "  2. Click ⋮ (top right) → Check for updates / Reload"
echo "  3. Find 'Polar Analyzer' in the Local apps section"
echo "  4. Click Install (first time) or Rebuild (update)"
echo "  5. Configure Signal K URL and start the app"
echo ""
echo "Or via CLI on the HA device:"
echo "  ha store reload && ha apps install local_$ADDON_NAME   # first time"
echo "  ha apps rebuild local_$ADDON_NAME                      # update"
echo "  ha apps start local_$ADDON_NAME"
echo "  ha apps logs local_$ADDON_NAME"
