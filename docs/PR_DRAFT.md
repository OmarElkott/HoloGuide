# Fix P4 real demo and evaluation integration (DSH-10, EVL-01b)

Closes the six explicitly requested November 8 gaps as far as local runtime and
repository permissions permit. Base main; head fix/person4-final-integration.
Earlier P4 work is already merged as PR #2. Request P3 review; do not merge yet.

- Real one-click demo orchestrates existing P1 ScenarioRunner episodes through
  configured real inspectors, Workflow and audit. Displays each inspection frame
  and decision before the next tick. Explicit review/timeout presentation faults
  cannot hide natural real failures; overrides use identity/reason/control API.
  Mock demonstrations remain separately selectable; stop/reset are retained.
- DSH-07 indicator reads actual P2 nested inspector/model/default endpoint settings
  and avoids offline claims. Adds reproducible manual offline checklist.
- EVL-01b recognizes returned P2 timeout/failure diagnostics, including invoked
  Hybrid branches without counting stale skipped Gemma failures. Confirms the
  generator manifest, checks supplied image/config integrity, improves provenance,
  and reuses existing CSV/report tooling.
- Corrects stale README/writeup claims. All six task statuses have evidence and
  remaining acceptance actions. P2's documented measurements are treated as
  reported results until raw artifacts/provenance are accessible.
- GIT-01: actually created 12 missing task/area/priority labels. API reports main
  unprotected, no rulesets/CI workflows and no admin permission. Owner settings are
  documented; no protection modification or invented required CI check is claimed.

Validation: targeted non-simulator suite: 160 passed, 1 live acceptance test skipped.
Full pytest FAILED collection in six PyBullet-dependent modules. Both Windows
Python 3.12 and isolated portable 3.11 binary PyBullet installs failed; no substitute
simulator was installed. Local Ollama /api/tags timed out outside sandbox too.

Actual OpenCV and Hybrid process the archived PyBullet missing-gasket PNG from
P2's notebook through runner/CSV/report: both BLOCK, zero errors/timeouts/reviews,
Hybrid Gemma calls=0. This n=1 smoke check is not final evaluation or model-laptop
latency evidence. It is reproducible with scripts/person4_verify_sample.py.

Pending acceptance: live simulator/dashboard/demo, full dataset/all-inspector final
evidence and actual Wi-Fi-off/Gemma rehearsal on the provisioned demo laptop;
P2's raw archive and P1/P3 recovery/safety artifacts; owner branch protection.
No shared schemas, inspector/control internals or P1 scenario API changed.
Generated files/models/runtime remain ignored. Focused commits separate dashboard,
evaluation, tests/hygiene and documentation for review of this cumulative fix.
