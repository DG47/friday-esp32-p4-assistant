#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
idf_root="${FRIDAY_IDF_ROOT:-$project_root/work/esp-idf}"
idf_tools_root="${FRIDAY_IDF_TOOLS_ROOT:-$project_root/work/.espressif}"

if [ ! -f "$idf_root/export.sh" ]; then
    echo "Local ESP-IDF is missing from $idf_root" >&2
    echo "See firmware/README.md for setup instructions." >&2
    exit 1
fi

export IDF_TOOLS_PATH="$idf_tools_root"
. "$idf_root/export.sh" >/dev/null
cd "$project_root/firmware"
exec idf.py "$@"
