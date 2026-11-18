#!/bin/bash
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)" || exit 1
exec /bin/bash "$ROOT/install_official_pydrttools_optional.sh" "$@"
