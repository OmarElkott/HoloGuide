# How HoloGuide works

Current implementation: November 12, 2026. Setup is in [README](../README.md),
validation in [VALIDATION](VALIDATION.md), and the architecture in [architecture.png](architecture.png).

1. The operator places a part in a four-step PyBullet assembly. Workflow's order
   guard rejects premature placement before inspection.
2. The simulator renders a PNG. An inspector receives only the image and StepSpec,
   not simulation truth or scoring labels.
3. OpenCV detects coloured parts. Hybrid uses OpenCV first and consults local Gemma
   when uncertain. Gemma-only inspection and an always-explained Hybrid factory are
   also available. A configured backend never substitutes mocks on failure.
4. Deterministic rules use the validated Observation to produce PASS, BLOCK,
   NEEDS_REVIEW or HOLD. Workflow alone authorizes progress. A confidently wrong
   observation can still cause an incorrect decision; tests are not a guarantee of
   physical-world accuracy. The controller cannot reconstruct past order from an image.
5. The dashboard shows the current scene, last inspection, decision, correction,
   confidence and verified progress. Overrides require identity and reason. HOLD
   acknowledgement does not approve the step. Removing verified parts invalidates
   approval. Decisions and interventions are logged with JSONL download.

## Demo and evidence

The one-click mock demo runs explicitly scripted fault, correction, review,
timeout/HOLD and override episodes. Real scenarios use the selected image inspector
and advance one tick at a time. The rehearsal app uses rendered scenes with prewritten
observations. None of those scripted modes is a finished three-minute recording or
proof of live Gemma inference.

The dataset generator creates 200 images per dataset with labels kept separate.
Evaluation scores independent images; recovery runs sequential correction trials.
Correct recovery controls complete without an injected fault; fault trials require
BLOCK, correction and PASS. P2's reported measurements are in [decisions](decisions.md).
Hybrid made no Gemma calls on those clean datasets; its numbers reflect OpenCV.
Raw generated artifacts are excluded from this source ZIP.

Localhost model metadata does not monitor network activity. Install dependencies
and provision the model before an offline rehearsal. Final video, backup, clean
notebook execution, second-laptop Wi-Fi-off run and submission/link verification
remain separate acceptance steps. This package does not establish their completion.
