# Fix ZIP provenance and mock startup; reconcile integration documentation

Source ZIP exports have no Git history, so the evaluation provenance test failed
and Git lookup could attribute them to an enclosing repository. Provenance now
records an explicit unavailable revision plus a runtime source fingerprint for
archives and hosts without working Git. Real Git revision/dirty metadata is retained
when available, with bounded subprocess execution. Tests cover archive identity,
source changes, newline normalization, missing/timed-out Git and dirty checkouts.

Mock dashboard startup imported PyBullet through the scenario launcher. Simulation
imports now occur only after the launcher confirms a real session, preserving the
README's mock-only development path.

README and P4/P1 handoffs now describe the included simulation, inspectors and
backend factories consistently. Historical results are distinguished from current
validation and final submission acceptance. See docs/VALIDATION.md for test output,
environment limits and the full-suite command. No real-model evaluation, hardware
offline rehearsal, remote merge or submission is claimed by this change.
