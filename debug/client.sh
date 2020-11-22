#!/bin/bash

set -e
set -o pipefail

RPIVIDCTRL_DEBUG_DIR="$(dirname "$(readlink -f "$0")")"
# shellcheck source=./plot.sh
source "$RPIVIDCTRL_DEBUG_DIR/plot.sh"

check_graphics_dir

CLIENT_PY="$(dirname "$RPIVIDCTRL_DEBUG_DIR")/rpividctrl_client.py"

TMPDIR="$(mktemp -d)"
cd "$TMPDIR"
echo "tmpdir: $TMPDIR"

GST_TRACERS="interlatency;framerate" python3 "$CLIENT_PY"

GSTSHARK_DATA_DIR="$(echo "$TMPDIR"/gstshark_*)"
show_plots "$GSTSHARK_DATA_DIR"

echo 'remove tmpdir'
rm -rf "$TMPDIR"