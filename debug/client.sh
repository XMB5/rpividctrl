#!/bin/bash

set -e
set -o pipefail

# param 1 (optional): gstshark tracers to use, semicolon separated
# RPIVIDCTRL_CONFIG (optional): path to config.json
# GSTSHARK_GRAPHICS_DIR: path of gst-shark/scripts/graphics directory

RPIVIDCTRL_DEBUG_DIR="$(dirname "$(readlink -f "$0")")"
# shellcheck source=./plot.sh
source "$RPIVIDCTRL_DEBUG_DIR/plot.sh"

check_graphics_dir

CLIENT_PY="$(dirname "$RPIVIDCTRL_DEBUG_DIR")/rpividctrl_client.py"

EXTRA_ARGS=()
if [ -n "$RPIVIDCTRL_CONFIG" ]; then
  EXTRA_ARGS+=('-c')
  EXTRA_ARGS+=("$(readlink -f "$RPIVIDCTRL_CONFIG")")
fi

TMPDIR="$(mktemp -d)"
cd "$TMPDIR"
echo "tmpdir: $TMPDIR"

GST_TRACERS="${1:-interlatency;proctime;bitrate;queuelevel;framerate}" python3 "$CLIENT_PY" "${EXTRA_ARGS[@]}"

GSTSHARK_DATA_DIR="$(echo "$TMPDIR"/gstshark_*)"
show_plots "$GSTSHARK_DATA_DIR"

echo 'remove tmpdir'
rm -rf "$TMPDIR"