# DSH-07: genuine offline rehearsal (manual, demo laptop)

Preparation is implemented; Wi-Fi-off evidence remains **pending**. The dashboard
shows actual inspector/model settings, not a claim of zero external traffic.
Codex has not disconnected any adapter or claimed to observe this rehearsal.

## Before disconnecting

1. Install repository requirements on the demo laptop. Confirm PyBullet works.
   On this P4 host binary installs failed for Windows Python 3.12 and portable
   3.11; a supported environment/build toolchain is needed. Don't install during rehearsal.
2. Provision Ollama and `gemma3:4b` beforehand. `ollama list` must show the model;
   record the full model ID, Ollama version, CPU/GPU, Python and package versions.
3. Create a new evidence folder, such as `outputs/offline-20261110-01`.
   Record `git rev-parse HEAD`, `git status --short` and `pip freeze` there.
4. Set these actual backend values in PowerShell:

```powershell
$env:HOLOGUIDE_BACKEND_FACTORY = 'src.simulation.backend:create_session'
$env:HOLOGUIDE_INSPECTOR_FACTORY = 'src.vision.factories:hybrid'
$env:HOLOGUIDE_MODEL_NAME = 'gemma3:4b'
$env:HOLOGUIDE_MODEL_ENDPOINT = 'http://localhost:11434'
Invoke-RestMethod http://localhost:11434/api/tags
```

Hybrid's clear scenes may make zero Gemma calls. To demonstrate actual local
model use, also run at least one `src.vision.factories:gemma` inspection offline.
Don't call zero Gemma invocations evidence that the model ran.

## Perform and record

1. Save work and launch a separate local terminal. Manually switch Wi-Fi off.
   Disconnect other external uplinks if claiming an offline laptop. Do this yourself;
   the Codex connection may stop. Record the time and adapter state/screenshot
   (`Get-NetAdapter` is read-only). Don't rely solely on the localhost label.
2. Restart the dashboard while disconnected:

```powershell
.venv/Scripts/python -m streamlit run src/dashboard/app.py --server.address localhost --browser.gatherUsageStats false
```

3. Select Real, start/reset, place base plate and Inspect. Save the actual frame,
   status, confidence, correction message, progress and session audit path.
4. Run scripted real demo. Verify all physical fault/correction episodes finish;
   capture review, injected timeout/HOLD, acknowledgement and recorded override.
   Stop/reset once to confirm cleanup. Keep failures if any; don't substitute mocks.
5. In a separate new Gemma session (`HOLOGUIDE_INSPECTOR_FACTORY=src.vision.factories:gemma`),
   inspect a real rendered image and retain its Observation/audit. Confirm a real
   local model call rather than a Hybrid CV-only branch. No model pulling/downloads.
6. Download audits and capture the final dashboard. Record start/end, operator,
   revision, inspector/model ID, adapter state, outcomes and exact errors.
7. Reconnect manually when finished. Share the evidence archive with the team.

Physical disconnection plus successful local runs proves that run worked without
external connectivity. It does not by itself measure attempted external calls.
If claiming zero attempted external calls, also retain a reviewed traffic trace.
No screenshots, successful outcome, or zero-traffic values are prefilled here.
