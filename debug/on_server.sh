#!/bin/bash

# script that runs on the server as part of server.sh
# do not execute this script directly

# param 1: list of tracers for gstshark
# param 2: tmpdir (that doesn't exist yet) for gstshark data dir to go in

set -e
set -o pipefail

echo 'on_server.sh running'

RPIVIDCTRL_DIR="$(dirname "$(dirname "$(readlink -f "$0")")")"

mkdir "$2"
cd "$2"
GST_TRACERS="$1" python3 "${RPIVIDCTRL_DIR}/rpividctrl_server.py"