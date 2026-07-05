"""Reviewer calibration package.

A single, corpus-free approach: derive per-axis quality FLOORS from the reviewer's
scores on the golden lessons (``rubric_floors``) and gate any lesson scoring below
a floor. Issue findings stay advisory; the SCORES carry the load-bearing gate.

Import the submodules directly:

- ``calibration.rubric_floors`` — pure floor math (offline).
- ``calibration.rubric_run`` — live driver (real reviewer + golden loading).
"""
