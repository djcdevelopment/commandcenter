# 0020 — Phase 4 health facets and compatibility

## Status

Implemented in code and tests; live gateway restart intentionally deferred.

## Contract

`doorcheck` exposes four stable machine-readable facets:

| Facet | Healthy when | Non-healthy statuses |
|---|---|---|
| `process_listener` | TCP listener accepts connections | `down` |
| `authentication` | authenticated `kernel_status` succeeds | `unknown`, `failed` |
| `mcp_surface` | handshake succeeds and the expected tool manifest matches | `down`, `failed`, `degraded` |
| `backend_dependency` | the configured default backend is ready | `cold`, `failed` |

The aggregate `door` facet is the first three facets. Default `doorcheck` answers
the door facet, so backend cold is advisory and exits zero. `--strict` requires
all four facets. `--facet` selects one facet. Exit 1 means the requested facet
is not healthy; exit 2 is reserved for hard configuration failures.

Existing human-readable lines and legacy JSON keys remain present. New consumers
should use `facets`, `requested_facet`, `strict`, and `hard_failure`, not prose.

## Liveness

`GET /healthz` is unauthenticated and returns only `{"status":"ok"}`. It is a
minimal process/liveness contract and intentionally contains no caller, tool,
backend, path, secret, or configuration information.

## Hardening compatibility

Input paths containing `..` are rejected rather than normalized. This is an
intentional hardening incompatibility: direct in-scope paths are required, and
the rule must not be weakened for speculative external callers.

## ACL degradation

`icacls` remains best-effort. `callerctl list` now exposes registry ACL state as
`secured`, `degraded`, or `unknown` while preserving its historical JSON array
shape. A successful registry write with failed lockdown is therefore visible.
