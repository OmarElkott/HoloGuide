# DSH-02/03/09/10, EVL-02: complete mock dashboard flow and reproducible evaluation reports

Base: `main` (rechecked: no remote `dev` branch)
Head: `feat/person4-evaluation-dashboard`
Suggested status: Draft; P3 review required before merge.

Extends the existing Person 4 branch rather than duplicating it. Adds validated
camera-image presentation with honest missing/mock placeholders, all workflow
status banners, last-inspection/current-step distinction, spinner and measured
latency, stale-submission protection, session cleanup and explicit real-backend
adapters. Overrides and progression remain under existing control authority.

One-click mock demo executes existing scenario loops plus review, injected timeout,
HOLD recovery, reinspection and an auditable demonstration override. Progress,
stop/reset, transcripts and audit paths are shown. It is separate mock episodes,
not the final integrated timed camera/model demo.

Evaluation retains `src/evaluation/`, separates labels from inspector inputs,
supports explicit inspector factories/signature adaptation, records provenance,
retains failures and counts explicit timeouts. A standalone saved-CSV report
generator produces comparisons with unavailable measurements as N/A and optional
P1 recovery/P3 safety evidence attachments. No real model results are invented.

Validation:

- 109 tests passed in the working checkout.
- 109 tests passed in a clean local Git clone with a newly created venv after
  installing the documented mock-development dependency subset; pip check passed.
- Full `pip install -r requirements.txt` FAILED: PyBullet 3.2.7 selected a Windows
  source build requiring absent Microsoft Visual C++ 14.0+ Build Tools.
- Evaluation/report CLI help smoke checks and git whitespace checks passed.
- No second-laptop, Wi-Fi-off hardware, real-model or final submission run claimed.

Dependencies:

- Roadmap is now present and audited: see docs/PERSON4_WRITEUP.md for all 20 tasks.
- P1's unmerged AssemblyEnv branch has placeholder placement/removal; no dataset,
  scenario library/panel or recovery evidence. Inspected, not merged here.
- P2's real inspectors/timeouts and model/dataset integration are unavailable.
- Manifest remains a provisional adapter pending P1 confirmation.
- Final evaluation, teammate writeup sections, offline evidence and submission
  checks remain blocked; labels/protection need leader agreement, not admin edits.

This cumulative PR exceeds the roadmap's approximate 300-line preference because
the previously pushed branch still has no PR and its history is preserved. New
dashboard, report, correction and documentation work is separated into focused
commits for review; shared history was not rewritten. Shared schemas and teammate
internals are unchanged. No generated outputs, models, secrets or venvs are staged.
