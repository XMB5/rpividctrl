#!/bin/bash

set -e
set -o pipefail

if [ -z "$RPIVIDCTRL_SSH" ]; then
  echo 'pass server ssh address in RPIVIDCTRL_SSH environment variable, example: pi@10.2.39.47' >&2
  exit 1
fi

TRACERS="${1:-'interlatency'}"

RPIVIDCTRL_DEBUG_DIR="$(dirname "$(readlink -f "$0")")"
# shellcheck source=./plot.sh
source "$RPIVIDCTRL_DEBUG_DIR/plot.sh"

check_graphics_dir

RPIVIDCTRL_DIR="$(dirname "$RPIVIDCTRL_DEBUG_DIR")/" # trailing slash=copy contents of directory
REMOTE_RPIVIDCTRL_DIR="/tmp/rpividctrl_server_debug/"
REMOTE_TMPDIR="/tmp/rpividctrl_server_debug_tmp.$(openssl rand -hex 16)"

echo 'copy files to remote server'
rsync -a --exclude '.git' --exclude '.idea' --exclude '.gitignore' --include 'debug/on_server.sh' --exclude 'debug/*' --exclude '__pycache__' \
 "$RPIVIDCTRL_DIR" "$RPIVIDCTRL_SSH:$REMOTE_RPIVIDCTRL_DIR"
REMOTE_COMMAND="$REMOTE_RPIVIDCTRL_DIR/debug/on_server.sh '$TRACERS' '$REMOTE_TMPDIR'"
echo "remote command: $REMOTE_COMMAND"
ssh -t "$RPIVIDCTRL_SSH" "$REMOTE_COMMAND" || true
LOCAL_TMPDIR="$(mktemp -d)"
rsync -a "$RPIVIDCTRL_SSH:${REMOTE_TMPDIR}/gstshark_*/" "$LOCAL_TMPDIR"
echo "local tmpdir: $LOCAL_TMPDIR"

show_plots "$LOCAL_TMPDIR"
echo 'remove local tmpdir'
rm -rf "$LOCAL_TMPDIR"
echo 'remove remote tmpdir'
# shellcheck disable=SC2029
ssh "$RPIVIDCTRL_SSH" "rm -rf '$REMOTE_TMPDIR'"
