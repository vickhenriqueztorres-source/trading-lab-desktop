# Third-party notices

## IQ Option read-only protocol reference

The minimal read-only IQ Option authentication/WebSocket implementation in
`packages/brokers/iqoption/community_read_only.py` was derived from the protocol flow documented by
the MIT-licensed `victalejo/iqoptionapi` project, commit
`acac6e08333466ae188c7dfa7fd2a03174e34ca2` (2026-05-11).

Copyright (c) the iqoptionapi contributors. Used under the MIT License.

The Trading Lab implementation does not vendor the third-party package and intentionally omits all
financial order APIs. IQ Option does not provide an official public API contract for this flow, so
compatibility may change without notice.
