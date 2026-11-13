# Person 4 validation - November 12, 2026

Scope: the extracted `HoloGuide-main (1).zip`, updated locally under
`zip-review/HoloGuide-main`. The original Downloads ZIP and other checkout are
unchanged. The existing checkout's Python 3.12 virtual environment supplied the
available dependencies. An additional isolated Python 3.11 environment was tried,
but dependency installation there failed before full test execution.

## Changes verified

- ZIP evaluation no longer assumes a Git commit exists. Provenance records explicit
  unavailable Git fields and a deterministic runtime-source SHA-256. Git checkouts
  retain their revision/dirty state. Git absence/timeouts do not abort evaluation.
- Source identity excludes generated outputs, accounts for runtime source changes
  and normalizes CRLF/LF line endings. Source files, config YAML and requirements
  are covered. The hash does not replace model/dataset/environment provenance.
- Mock dashboard startup no longer imports PyBullet through the real launcher.
- README, P4 writeup, P1 handoffs and system explanation agree on included modules,
  built-in factories, generated-artifact availability and remaining acceptance.
  Old roadmap/control progress and PR notes are labeled as historical or replaced.

## Current results

| Check | Result |
|---|---|
| Full `python -m pytest -q` | Blocked: 6 collection errors, all missing PyBullet |
| Available suite, excluding six simulation-dependent files | 156 passed, 6 skipped, 6.18 s |
| Skip reasons | Six rendered CLI cases require PyBullet |
| `python -m pip check` in existing Python 3.12 environment | No broken installed requirements |
| `python -m compileall -q src tests` | Passed |
| Evaluation runner/report `--help` | Passed |

The passing subset includes Streamlit AppTest startup/reset/error handling, workflow,
control, report/evaluation and the added provenance tests. This is not a full-suite
pass, real-model benchmark, fresh-machine install or offline rehearsal. `pip check`
checks installed package metadata; it does not establish that PyBullet is installed.

Raw output: [full suite](validation-2026-11-12/full-suite.txt),
[available suite and skip reasons](validation-2026-11-12/available-suite.txt),
[environment/source identity](validation-2026-11-12/environment.json).
Earlier 166/213/219-test totals in dated teammate documents belong to other hosts
and revisions, not this validation. P2's reported model/recovery numbers remain in
[decisions.md](decisions.md); their raw artifacts were not supplied in this ZIP.

Before pushing, the fixes were applied to a separate Git worktree based on current
main `3073aa6525ffc889d96bd41949e7cecb5ff56df5`. The original ZIP's runtime source
matched that base. A focused Git-checkout run of `test_provenance.py`,
`test_evaluation_report.py`, `test_dashboard.py` and `test_dashboard_features.py`
passed **18 tests in 3.80 seconds**, covering revision metadata with real Git history
as well as mock dashboard startup. The existing Person 4 branch was left intact.

## Full-suite blocker and exact next check

Installing `requirements.txt` selected PyBullet 3.2.7 source on both Windows
Python 3.12 and isolated Python 3.11.18. Both builds failed with:
`Microsoft Visual C++ 14.0 or greater is required`. Requiring binary PyBullet
packages also found no compatible wheel, including against the official PyPI index.
Python 3.11 alone therefore does not fix this host. No compiler, Windows SDK or
system-wide Python was installed by this change.

On a host with Microsoft C++ Build Tools and the Windows SDK, or an actually
compatible PyBullet wheel, run from the project root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

Repeat the complete suite without exclusions. On this host the newly created
project-local Python 3.11 `.venv` is incomplete because installation failed; install
requirements before using it. The passing subset above used the pre-existing
`HoloGuide-main/.venv/Scripts/python.exe` outside this extracted folder.

To reproduce the available subset with that existing interpreter:

```powershell
..\..\HoloGuide-main\.venv\Scripts\python.exe -m pytest -q -ra --ignore=tests/test_cv_inspector.py --ignore=tests/test_hybrid_inspector.py --ignore=tests/test_partner1.py --ignore=tests/test_partner1_deliverables.py --ignore=tests/test_simulation.py --ignore=tests/test_vision_factories.py
```

Final notebook execution, target-laptop Gemma demonstration, real rendered scenario
rehearsal, Wi-Fi-off/second-device acceptance, final video and submission links are
still open. No submission or remote repository update was performed.
