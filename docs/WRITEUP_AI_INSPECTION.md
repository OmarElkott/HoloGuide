# Writeup section: AI inspection and why Gemma 3 4B (DOC-03d)

*Partner 2 section for the final writeup (DOC-03b). Numbers are measured; sources and commands are in
[`decisions.md`](decisions.md).*

## The inspector's job and its limits

The inspector looks at one rendered camera image and reports what it sees, as an `Observation`. That report lists
the parts detected, missing and wrong, a confidence from 0 to 1, and a one-sentence rationale. Three things are
deliberately kept out of its hands:

- **It never decides.** The deterministic rules engine turns the Observation into pass, block, needs review or
  hold. The workflow advances only on a pass or on a logged operator override. The rationale is shown to the
  operator but never used to authorise anything.
- **It never sees ground truth.** Only the image path and the step definition reach it. Dataset labels stay in the
  evaluation code.
- **It never crashes the workflow.** Any failure (unreadable image, malformed model output after one retry,
  connection error, out-of-memory, or a call longer than `ai_timeout_s` = 20 s) comes back as an Observation with
  confidence 0.0. The rules map that to **Hold**, a safe stop that an operator clears without approving the step.

Because the AI can only *report*, its mistakes are bounded. An uncertain report goes to a person. The one
failure the rules cannot catch is a report that is **confidently wrong**, and our evaluation focused on finding
exactly those.

## Why Gemma 3 4B

| Requirement | How Gemma 3 4B meets it |
|---|---|
| Fully offline, no cloud API | Open weights served by Ollama on `localhost`; the demo never downloads anything |
| Fits a 6 GB laptop GPU | The 4-bit `gemma3:4b` needs 2.9 GB and runs 100% on the RTX 3060 Laptop GPU |
| Reads images | Natively multimodal, with no separate vision encoder pipeline to build |
| Machine-readable output | Ollama's schema-constrained JSON, validated again with Pydantic |
| Responsive enough for an operator | Median 3.0 s, p95 3.3 s per image (VIS-12, 40 images); 0 timeouts in 841 calls |

Larger vision models would not fit the GPU alongside the simulator and dashboard. Smaller text-only models
cannot read images at all.

## Three inspectors, one contract

All three inspectors implement `inspect(image_path, step) -> Observation`, so the workflow, dashboard and
evaluation runner can switch between them with a one-line factory (`src.vision.factories:cv|gemma|hybrid`).

1. **Gemma.**
   - The model answers a schema-constrained question.
   - Code, not the model, derives confidence from a certainty word: clear / partly clear / unclear map to 0.90 / 0.60 / 0.30.
   - Missing parts are inferred only when the model says its view is clear.
2. **OpenCV.** Colour masks are generated from the part colours in `assembly.yaml`, and each part's share of the
   image becomes a confidence:
   - **Confidence scale:** clearly present or clearly absent scores 0.75 to 0.95; an in-between amount scores 0.50,
     which sends the step to review.
   - **Bolts:** counted as compact blobs, and exactly four are required.
   - **Covers:** must be box-shaped.
   - **Unknown objects:** colours that no part explains also send the step to review.
3. **Hybrid (the default).**
   - OpenCV decides presence. Gemma is consulted only for parts OpenCV is unsure about, and only Gemma's answers for
     those parts are used.
   - If Gemma disagrees with OpenCV, is itself unsure, or fails, the step goes to operator review. Gemma therefore
     cannot turn an uncertain image into a pass unless OpenCV was uncertain about exactly those parts and Gemma is
     clear about them.

## What the evaluation taught us

We evaluated on two 200-image datasets: v1 with a fixed scene, and v2 with randomised camera, lighting and part
offsets. Each has 80 correct images and 40 each of missing part, wrong part and wrong order. Accuracy means the
final rule decision matched the label.

**Run 1 exposed priming.** The first prompt told Gemma which step was being checked and where to look. The model
then reported what *should* be there:
- a base plate on an empty table;
- the green decoy reported as the blue cover;
- four bolts that had not been placed.

Missing-part accuracy was 25% and wrong-part accuracy 25 to 50%. These were the dangerous kind of error: the
mistake was present and the step passed.

**The fix was to ask less.** Prompt v2 is step-agnostic:
- It asks one true/false question per colour ("Is there a green square piece?").
- Answers are keyed by colour, never by part name, and code maps them back to parts.

A list-of-colours variant was rejected during a small pre-check because the model simply copied the whole list for
an empty table. Cropping the image to the assembly made no difference, so it is off.

| Inspector (dataset v2, held out) | Missing | Wrong part | Wrong order | False blocks | Median latency |
|---|---:|---:|---:|---:|---:|
| Gemma, prompt v1 | 25% | 25% | 97.5% | 1.25% | 2.4 s |
| Gemma, prompt v2 | 100% | 100% | 85% | 7.5% | 3.2 s |
| OpenCV | 100% | 100% | 100% | 0% | 0.014 s |
| Hybrid | 100% | 100% | 100% | 0% | 0.018 s |

On v1, prompt v2 reached 100% on every type. On the held-out v2, all twelve remaining Gemma errors came from a
single scene: under some lighting, Gemma misses the orange gasket when it lies on the bare plate. That produced
six false blocks and six false accepts. We did not tune further against v2, so it remains a fair test.

**Decision (go/no-go): option B, Hybrid.**
- Gemma alone narrowly meets the team's "Gemma primary" bar: at least 85% on missing and wrong parts, at most 8% false blocks, at most 10 s median.
- It is still the only inspector that produced false accepts.
- OpenCV is perfect on these renders and about 200 times faster.
- Gemma therefore serves as the tie-breaker and, optionally, the explainer.

With OpenCV inside the full loop, all three mistake scenarios were caught and recovered in 20 of 20 trials each.

## Honest limitations

- **Synthetic images.** These are clean renders with distinctive colours and no shadows. OpenCV's perfect score
  depends on that, and a real camera would need recalibrated thresholds. Nothing here proves performance on a
  physical line.
- **Hybrid results equal OpenCV's.** Gemma was consulted on 0 of 400 dataset images because OpenCV was never
  unsure. The Gemma branch of the Hybrid is verified by unit tests, not by these measurements.
- **A small model is a fragile witness.** Wording changed Gemma's missing-part accuracy from 25% to 100%. Prompts
  are versioned (`HOLOGUIDE_PROMPT_VERSION`), and any prompt change should be re-evaluated on held-out data before
  it is trusted.
