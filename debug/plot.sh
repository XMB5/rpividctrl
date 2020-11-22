#!/bin/bash

# functions for plotting gstshark data

check_graphics_dir() {
  if [ -z "$GSTSHARK_GRAPHICS_DIR" ]; then
    echo 'set GSTSHARK_GRAPHICS_DIR environment variable to gst-shark/scripts/graphics directory' >&2
    exit 1
  fi
  if ! [ -f "${GSTSHARK_GRAPHICS_DIR}/gstshark-plot" ]; then
    echo 'could not find gstshark-plot in GSTSHARK_GRAPHICS_DIR' >&2
    exit 1
  fi
}

show_plots() {
  # first param: gstshark data dir
  # shellcheck disable=SC2164
  cd "$GSTSHARK_GRAPHICS_DIR"
  ./gstshark-plot "$1" -p
}