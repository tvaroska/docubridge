"""Demo hosts (S0-arch-15) — the fenced demo-host package inside ``bridge/``.

Demo-host glue lives here: it MAY name demo skills, seed synthetic data and read the
``wiki/evals/`` corpus (e.g. ``bridge.demos.rfp_demo``, the local RFP demo host — a
``uvicorn --factory`` target). The core never does: no module outside ``bridge/demos/``
imports this package, and no string constant outside it names an installed skill
(``tests/test_isolation_guard.py::test_core_never_imports_demo_hosts`` /
``::test_core_names_no_skill``).

Deliberately imports nothing (keeps fresh-process imports light and clear of the
circular-import trap); import the demo module you want by its full path.
"""
