# Three-minute demo and recording plan

## Before recording

Use a 1920x1080 desktop, 100–125% scaling, a readable dashboard window and no notifications. In an already installed screen recorder (for example OBS), capture the application window, select the actual microphone and record a 20-second local test. Speak at normal volume, check that text is readable and audio is not clipping, then play the file back with headphones. Do not install a recorder or upload anything without authorization.

Provision the real inspector and model in advance. Warm it up with an ordinary image, verify the selected factory and model identifier, and measure real latency. Then turn Wi-Fi off manually and rehearse the complete sequence. Record only genuine results. If running scripted tests or the mock demo, keep the mock label visible and explicitly say it is a control demonstration.

Prepare separate fresh sessions for normal flow, missing gasket and wrong cover. Episodes are independent assemblies; never splice them as if the workflow silently rewound. Keep logs and the rendered images for every take. Have the architecture image and actual evaluation table ready. The included demo-preview.gif is a silent rendered rehearsal asset with scripted observations, not the final video or an AI demonstration.

## Narration and shot list

| Time | Screen / action | Narration |
|---|---|---|
| 0:00–0:20 | Architecture, then dashboard with inspector identity | P4: “HoloGuide explores how an offline assistant can catch assembly errors. Vision reports evidence; deterministic rules decide whether the next step is allowed.” |
| 0:20–0:50 | Fresh correct scenario; base and gasket pass | P1: “This is our PyBullet workstation. The gasket remains visible around the cover, and the camera supplies only an RGB image. Evaluation labels are stored separately.” |
| 0:50–1:20 | Fresh missing_gasket episode; base passes, premature cover blocks | P2: “Here the gasket is missing and the cover belongs to a later step. The workflow blocks the assembly based on inspection evidence. This is a deliberate fault injection, not an authorized operator action.” |
| 1:20–1:50 | Remove premature cover, insert gasket, re-inspect, then cover/bolts | P3: “We remove the cover, insert the gasket and inspect again. Only a pass authorizes progress. The correction and every decision remain in the audit.” |
| 1:50–2:20 | Fresh wrong_cover episode, green decoy blocks; separate guard example | P1/P2: “A green decoy is not the correct cover. Separately, the action guard can reject a premature action before asking the model. Our test runner also injects that part physically to test the visual path.” |
| 2:20–2:40 | Explicitly labeled injected review/timeout episode | P3: “For this control test we inject uncertain evidence and a timeout. The workflow requires review or holds safely. This is fault injection, not a measured model failure.” |
| 2:40–2:55 | Typed override reason and audit entry | P3/P4: “An operator may approve only with a recorded reason. Oversight is visible and attributable.” |
| 2:55–3:00 | Actual evaluation table, or honest unavailable-results caption | P4: “AI sees, rules decide, people oversee.” |

Do not read invented accuracy numbers. If real evaluation is unfinished, say “Real-model evaluation is pending” and omit performance claims. Use actual UI messages; the rules may prioritize wrong order over missing-part wording in the missing-gasket episode.

## Takes, backup and handoff

1. Record a 20-second screen/audio check and review it locally.
2. Rehearse to 3:00 or less. Use the runner's single-step controls for timing; model latency is real and must not be disguised.
3. Record take 1; review start-to-finish for labels, audio, unexpected overrides, exposed personal information and missing evidence.
4. Record the final take and a second backup. Save the recorder's resilient native format, then export/remux to MP4 if supported by the installed recorder. Do not erase the source.
5. Name files `HoloGuide-final.mp4` and `HoloGuide-backup.mp4`, and save a second local copy on another drive. Check both files play, are <=3:00, and show all promised episodes.
6. Upload only after explicit permission. Verify the final accessible URL and competition requirements at that point. No upload or competition submission was performed here.

The physical microphone check, recorder operation, Wi-Fi-off rehearsal, final GPU/model demonstration, second-device playback and final recording require the user's PC interaction. No final video is claimed to exist.

A playable silent storyboard is now available at outputs/HoloGuide-scripted-rehearsal-v2.mp4. It covers all four rendered episodes in 28.5 seconds with a permanent scripted-observation label. This supplements the GIF; neither is the required narrated screen recording.

## November 8 evidence and exact handoff

For the readily runnable local demonstration choose `src.vision.factories:cv` and identify it on camera as OpenCV, not Gemma. The measured regression results are 400/400 correct decisions on existing synthetic V1/V2 images; recovery completed 80/80 trials, with 60/60 fault recoveries. Cite the raw evidence archive and do not generalize these results to a physical camera or Gemma.

A new silent replay can be made with `python -m src.simulation.recovery_video outputs/nov8-verification/recovery-cv --output outputs/recovery-cv-trial0.mp4`. It presents trial 0 for each scenario, never silently selecting only successful frames. The sidecar declares fixed presentation timing and hashes. This is a useful recording insert, not a microphone test or screen recording.

For the 20-second microphone test, say: “HoloGuide uses an RGB image to inspect an assembly. OpenCV reports visible parts; our rules decide whether to proceed. These labels are added to the presentation, not supplied to the inspector.” Play the actual file back, verify audible narration and legible dashboard text, and record its filename and pass/fail in the final report. Then follow the timed script above for take 1, final and backup.

Browser automation could not open localhost because of a saved access permission. Manual visible acceptance and genuine Wi-Fi-off rehearsal therefore remain pending. Do not present the automated controller test as proof of those checks. No recording software or model was installed during this finalization.
