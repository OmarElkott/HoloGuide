# HoloGuide: Person 4 writeup and integration handoff

Updated November 12, 2026. Current test evidence and unresolved acceptance items
are in [VALIDATION.md](VALIDATION.md); setup commands are in [README](../README.md).
This replaces the November 1 dependency-blocked draft.

## Problem, architecture and implemented integration

HoloGuide assists a four-step virtual assembly: base plate, gasket, cover and bolts.
It identifies missing/wrong/visible future parts and presents corrections. It is a
simulation-only prototype; synthetic results do not establish physical-world accuracy.

The simulator renders an image. An inspector returns a validated Observation.
Deterministic rules produce a Decision; Workflow alone changes progression.
The dashboard uses guarded workflow methods for placement, inspection, removal,
HOLD acknowledgement and identified, reasoned overrides. Labels and simulator
truth remain outside the image inspector. Free-form model explanations never
authorize progression. See [architecture](architecture.png).

All teammate modules are now present. `src.simulation.backend:create_session`
constructs the real dashboard session with the explicitly selected
`HOLOGUIDE_INSPECTOR_FACTORY`. Built-in cv, gemma and hybrid factories accept
StepSpec directly. No inferred signature or silent mock fallback is used.
The included dataset generator supplies the evaluation manifest contract.

## Dashboard behavior

The dashboard displays workflow state, verified progress, current instruction,
correction, inspector/confidence/rationale, camera image and persisted audit.
Current scene and last inspection are distinct. Missing/corrupt/mock camera input
gets an explicit placeholder. Stale inspection and override submissions are guarded
by revision tokens. Reset closes supported environment resources and retains audits.
Immediate browser-disconnect cleanup is not guaranteed; reset before leaving.
Mock mode can start without PyBullet; the real scenario launcher imports simulation
only for real sessions.

The one-click scripted mock demo shows normal/fault/correction episodes, review,
injected timeout, HOLD recovery and an identified demonstration override. The real
scenario launcher uses rendered images and the selected inspector, one tick at a
time. `rehearsal_app.py` is separately labeled scripted observation playback.
These are different evidence types. Final timed recording/offline acceptance is
still pending and is not implied by unit tests.

## Evaluation and reproducibility

The runner evaluates independent images, saves all failures, reports exact decision
agreement by mistake type, false-block rate, HOLD/review counts, explicit timeouts
and median/p95 latency. Labels stay outside inspector calls. Latency covers inspect,
validation and rules, excluding rendering/artifact writes. Recovery runs separately
measure sequential BLOCK, correction and PASS behavior.

Provenance records dataset/image hashes, full configuration, inspector/model/endpoint,
package/platform versions, Git revision/dirty state when available, and a runtime
source fingerprint. ZIP exports and unavailable Git retain null Git fields with
an explicit unavailable status and a source SHA-256; no revision is invented.
Saved-CSV reporting works without rerunning a model and flags different manifests.
Missing inspector results remain N/A. Record target hardware/model digest manually.

## Results and limitations

[decisions.md](decisions.md) contains P2's reported November 7 synthetic measurements
and OpenCV recovery trials. OpenCV and Hybrid were reported at 100% decision accuracy
on two 200-image synthetic datasets. Hybrid consulted Gemma on zero dataset images,
so these results exercise OpenCV. Gemma's held-out wrong-order accuracy was 85%,
with 7.5% false blocks on correct cases. These are teammate-reported measurements;
the source ZIP excludes their raw generated artifacts and this P4 change does not
reproduce the live model benchmark. Preserve original artifacts for submission.

Distinct synthetic colours and clean lighting limit generalization. Still images
show visible future parts, not historical order. Missing-gasket scenario wording
can say incorrect order because future-part detection precedes missing-part checks.
Localhost configuration and disabled telemetry do not prove zero network traffic.

## Remaining final acceptance

P1: final three-minute video, backup recording and target-hardware scenario rehearsal.
P2: verify the installed Ollama/model and demonstrate an actual Gemma call on the
presentation laptop. P3: supply any final safety evidence required by the writeup.
P4/team: execute notebook on a clean environment, retain final run artifacts, check
writeup numbers against the final CSV, perform second-laptop Wi-Fi-off rehearsal,
and verify public repo/notebook/video/submission links. No upload, release tag or
submission confirmation is claimed by this local validation.
