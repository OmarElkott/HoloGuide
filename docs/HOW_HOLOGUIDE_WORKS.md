# How HoloGuide Works (Complete System)

How HoloGuide is meant to work from start to finish once every roadmap feature is in place, with a note at the
end on where the project stands today (2026-11-07).

## The finished HoloGuide

HoloGuide is a fully offline laptop app. A person builds a virtual assembly on screen and HoloGuide checks every
step. An AI looks at camera images, fixed rules decide, and the operator keeps the final say, with every decision
recorded. It ships with a measured evaluation, a public notebook, a writeup and a 3-minute demo video.

The assembly has four steps: **base plate → gasket → cover → 4 bolts**. HoloGuide catches three kinds of mistake:
a **missing part**, the **wrong part** (a green decoy cover) and parts in the **wrong order**.

## 1. Setup (once, before going offline)

- Clone the repo and install the Python requirements, following the README.
- Install Ollama and pull `gemma3:4b` ahead of time. The demo itself never downloads anything.
- Run the warm-up so Gemma is already on the GPU, then turn Wi-Fi off.

## 2. Starting a session

- Launch the Streamlit dashboard in **Real** mode. It connects the PyBullet simulator, the Hybrid inspector, the
  rules and workflow, and a fresh audit log.
- The **offline indicator** shows the model is running on `localhost`. The dashboard shows the camera view, step
  progress (1 of 4), a status banner and the decision panel.

## 3. One step, start to finish

1. **The operator picks a part.** The **order guard** checks it right away: a part from a later step (for example
   the cover during step 1) is refused before any picture is taken or any AI is called.
2. **The part is placed** in the simulation and the overhead camera takes a picture. The simulator's true answer
   never leaves the simulator.
3. **The inspector looks**, with an "Inspecting…" spinner and the time taken shown:
   - OpenCV checks colours and shapes in about 20 ms.
   - If OpenCV is unsure, Gemma 3 4B is asked to settle it (about 3 s).
   - The result is an **Observation**: the parts seen, the confidence, and a one-line reason.
4. **The rules decide**, checking in this order:

   | Check | Decision |
   |---|---|
   | A part from a later step is present | **BLOCK** (wrong order) |
   | The decoy part is present | **BLOCK** (wrong part) |
   | A required part is missing | **BLOCK** (missing part) |
   | The AI failed or took longer than 20 s | **HOLD** (safe stop) |
   | Confidence is below 70% | **NEEDS REVIEW** (a person must look) |
   | Otherwise | **PASS** |

5. **The dashboard shows the outcome:**
   - a coloured status banner;
   - a plain-language fix, e.g. "Gasket missing – insert it before attaching the cover";
   - who decided (the rule, the operator or the system), with the AI's confidence and reasoning.
6. **What the operator does next depends on the outcome:**
   - **Pass:** the workflow moves to the next step.
   - **Block:** fix the part (remove the wrong one or add the missing one) and inspect again. After repeated
     failed checks, the step goes to Needs Review.
   - **Needs Review:** the operator looks at the image and approves or rejects it with a **typed reason**, which
     is required.
   - **Hold:** a safe stop. The operator clears it with a reason, which doesn't approve the step, then
     re-inspects.
7. **Everything is logged.** Each action, observation, decision, override and hold goes into the audit log. The
   dashboard can view and filter the log and export it to CSV.

After step 4 (bolts) passes, the workflow shows **Complete**.

## 4. Built-in safety guarantees

- The AI can never move the workflow forward; only a PASS from the rules or a logged override can.
- A missing, wrong or out-of-order part can never reach a PASS.
- An invariant test checks this across 500 random action sequences and expects zero steps advanced without a pass
  or a logged override.
- Removing a part that was already approved cancels that approval.

## 5. Demo Mode (one click)

It plays the scripted 3-minute demo against the real scene while presenters narrate:

1. A normal run: the plate and gasket pass.
2. The cover is placed with no gasket → **blocked** with a fix message.
3. The gasket is added and the step is re-inspected → passes, and the workflow continues to bolts and Complete.
4. The green wrong cover is blocked, and an out-of-order part is refused by the order guard before any AI call.
5. A low-confidence image goes to **Needs Review**, and a deliberately slow AI call ends in **Hold**.
6. The operator approves an override with a typed reason, and it appears in the audit log.
7. Closing numbers from the evaluation.

## 6. Evaluation (proof it works)

- 200-image labelled datasets. The labels sit in a separate file that the inspector never sees.
- The evaluation runner scores Gemma alone, OpenCV alone and the Hybrid on accuracy per mistake type, false-block
  rate, and median and 95th-percentile latency.
- Recovery trials: 20 runs per scenario, each one blocked, fixed, re-inspected and passed.
- The report generator turns these results into the writeup tables.

## 7. Deliverables for submission

- The public **notebook**, which runs top to bottom.
- The **writeup**: problem, architecture, why Gemma 3 4B, control and safety, results and limits.
- The **3-minute video** of the demo, plus a backup recording.
- The **README** with install, run and offline instructions and the team roles.
- A **fresh-clone test** on a second laptop and an offline dress rehearsal, then the tagged `v1.0` submission on
  Nov 11.

## How that compares with today (2026-11-07)

**Already works:**
- sections 1–4 (setup, a session, the full step loop, the safety guarantees);
- the evaluation (results in [`decisions.md`](decisions.md));
- the notebook;
- the vision writeup section ([`WRITEUP_AI_INSPECTION.md`](WRITEUP_AI_INSPECTION.md)).

**Still missing:**
- Demo Mode on the real scene (it plays mock episodes today);
- the "Gasket missing" wording (the rules currently say "Incorrect assembly order");
- the remaining writeup sections and the final edit;
- the final evaluation run, filled into the writeup's tables;
- the video;
- the offline rehearsal and the fresh-clone test.
