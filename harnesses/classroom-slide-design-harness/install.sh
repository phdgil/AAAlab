#!/usr/bin/env bash
set -euo pipefail
PACK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec node "$PACK_ROOT/../../bin/aaalab.js" install classroom-slide-design-harness "$@"
