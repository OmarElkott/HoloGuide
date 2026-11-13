# Partner 1 delivery status

Current package review: November 12, 2026. This replaces the dependency-blocked
November 2 handoff. See [VALIDATION](VALIDATION.md) for current verification.

## Included source

- PyBullet assembly placement/removal/rendering and scene randomization.
- Two dataset-generation modes, each producing 200 images with separate labels.
- Four bounded scenario runners, recovery trials, render benchmark and video exporter.
- Real dashboard backend and rendered scenario launcher.
- Architecture image, simulation technical guide and recording plan.
- P2 cv/gemma/hybrid inspectors and the P3/P4 integration are present in this snapshot.

[decisions.md](decisions.md) records P2's November 7 synthetic inspection and OpenCV
recovery results. The generated raw datasets, recovery logs, benchmarks and storyboard
mentioned in older handoffs are excluded from this ZIP. Historical scripted
observations must not be described as AI performance measurements.

## Remaining acceptance

The current Windows host cannot install PyBullet without Microsoft C++ Build Tools;
no compatible wheel was found for its Python 3.11 or 3.12 environments. Consequently
the complete simulation suite and rendered rehearsal are not cleared by the current
P4 validation. Resolve that environment requirement, then repeat all tests.

Final three-minute screen/audio recording, backup video, target-laptop real-inspector
rehearsal, Wi-Fi-off check and second-device playback remain unverified in this package.
No final upload or submission confirmation is claimed. Setup and built-in factory
commands are in [README](../README.md); [technical guide](PARTNER1_TECHNICAL.md)
describes dataset/scenario protocols. P1 owns simulation/video acceptance; P2 owns
live model evidence; P4 coordinates final validation and submission documentation.
