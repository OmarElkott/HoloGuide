# HoloGuide: dashboard and evaluation writeup

## Scope and evidence

HoloGuide separates visual observations from deterministic assembly decisions.
The existing config defines cumulative assembly requirements; the control layer
evaluates Observation objects and owns progression. A dashboard presents the
returned decision, explanation, confidence, verified-step progress, and audit trail.
An operator may override blocked/review decisions only through the control API,
with an identifier and a non-empty typed reason. Workflow records authorization
before advancing. HOLD recovery acknowledges a resolved problem without approval.

This implementation uses a deterministic mock inspector and JSON scene snapshots.
It demonstrates UI/control integration, not visual recognition. The UI displays
visible mock parts rather than presenting synthetic snapshots as camera images.
Streamlit usage telemetry is disabled in project config; no connectivity probe
exists and no global offline claim is made.

## Evaluation method

The evaluation adapter invokes an inspector with image path and configured step.
Ground-truth status and mistake labels remain outside inspection. The returned
Observation is validated and evaluated using the existing RuleContext, including
cumulative requirements, decoys, and future parts. Cases are independent.
Exceptions are recorded as HOLD with error details, rather than dropped.
Per-case CSV, machine-readable summaries, and Markdown tables retain status counts,
error counts, exact decision accuracy by mistake type, false-block rate, and
measured latency statistics. False-block rate counts explicit BLOCK outcomes on
ground-truth PASS cases; review and hold are visible separately.

No evaluation dataset, frozen model, or real inspector is available in this
checkout. Therefore **no model accuracy, final latency, or final evaluation
results are reported**. Unit-test fixtures are not an evaluation dataset.

## Person 4 status

The requested HOLOGUIDE_ROADMAP.md was absent locally and on the remote main and
feat/control branches at inspection. Task IDs below follow the user's supplied
list; exact day ordering and acceptance criteria require the missing roadmap.

| Area | Implemented | Remaining dependency |
|---|---|---|
| TST-01 | Existing pytest setup retained; shared fixtures and README instructions added | Full simulation dependency install on target machine |
| EVL-01a/b | Metrics, mock-compatible runner, CSV/summary/table exports | Dataset manifest confirmation by dataset owner |
| DSH requested tasks | Streamlit mock dashboard, Workflow connection, typed override, progress, confidence, audit viewer/export, explicit Demo Mode | Roadmap mapping; real camera and Inspector components |
| Offline indicator | Honest mock/no-model-call indicator; network state unmonitored | Verify actual inference/network configuration on integrated machine |
| EVL-02 | Reproducible table generation from actual supplied cases | Actual labelled dataset for tables |
| EVL-05 | Runner ready | Final frozen dataset, inspector/model, target machine; final run pending |
| DOC-03a | Draft methods, architecture, limitations and integration notes | Teammate sections, final evidence and roadmap |
| DOC-03b | Not finalized | Final results, teammate review and submission requirements |
| DOC-06 | Checklist prepared below | Full fresh-clone installation and integrated submission rehearsal |
| GIT-01 | Existing ignore rules retained; PR template added; feature branch | Issue-label taxonomy requires team agreement; none invented |

## Ownership and integration handoff

Person 1 supplies AssemblyEnv/camera images; Person 2 supplies the real Inspector
and dataset with label contract. Person 3 owns Workflow, RuleContext, audit, and
override transitions. No shared schema or control internals were changed.
DashboardSession accepts an inspector for testing; its scene remains explicitly
mock. Real mode must be added after simulator capabilities and snapshot/display
contracts are confirmed. Do not silently route real images to MockInspector.
The provisional evaluation manifest is an adapter, not a new shared contract.

The remote has main and feat/control, without dev. This work uses a feature branch
and proposes main as PR base, consistent with CONTROL_MODULE_PROGRESS.md. The
user confirmed connected GitHub account OmarWaled234.

## Fresh-clone and submission checklist

Verification on this branch: 98 tests passed in the implementation checkout and
98 passed in a clean local Git clone, using the same installed venv. Streamlit
AppTest exercises placement, inspection, progress, blank-reason disablement, and
typed override. Whitespace checks passed. This does not establish full dependency
installation, real model performance, offline behavior or submission readiness.

- Clone the PR branch into a clean directory; install requirements in a new venv.
- Run `python -m pytest -q`; retain actual output and environment versions.
- Launch Streamlit from repo root; exercise correct assembly, missing/wrong/future
  part rejection, correction, override rejection of blank reason, and audit export.
- Confirm prior verified-part changes roll back approvals through Workflow.
- Install and verify teammate simulator and real inspector; rehearse HOLD/recovery.
- Freeze dataset/model/config; run evaluation without label leakage and preserve
  per-case results and generated tables with run provenance outside ignored Git output.
- Verify no external calls during the actual offline demonstration, including UI
  telemetry and model configuration; record evidence before claiming offline use.
- Collect teammate writeup sections, final results, screenshots, limitations, and
  required submission format; review all claims against actual evidence.
- Verify no secrets, models, generated outputs or venvs are staged; review PR diff.
- Confirm submission requirements and team approval. Do not merge automatically.
