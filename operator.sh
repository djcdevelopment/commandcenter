#!/usr/bin/env sh
# The root shim: one forwarding invocation and nothing else (D-102/D-103).
# Policy belongs in hearth/operator; run it inside the hearth-private venv.
exec python -m hearth.operator "$@"
