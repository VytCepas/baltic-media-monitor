# Plan — baltic-media-monitor

Done when: the GCP run has produced data/report/summary.json with H1, H2, H3, the scaling study and the live
stream, every correctness check passes on it, and every guarded behaviour has been seen red.

probed by: `just ci && just red-check && just reconcile && just stream-vs-batch` (all exit 0) and
`jq -e '.H1 and .H2 and .ai.H3.decided and .scaling.speedup and .live' data/report/summary.json`.

The two lines above are this project's done-gate. "Is this finished?" is
answered by running the probe, never by ticking a box. Change them only when the goal itself changes.
