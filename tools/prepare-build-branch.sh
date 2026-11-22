#!/usr/bin/env bash
# Break-glass / first-release path: prepare and push build/<tag>-nN from the operator machine.
# Usage: tools/prepare-build-branch.sh [--dry-run] [--tag X.Y.Z] [--remote fork]  (see ci/prepare_build_branch.py)
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
py=python3; command -v python3 >/dev/null 2>&1 || py=python
exec "$py" "$here/../ci/prepare_build_branch.py" --root "$here/.." "$@"
