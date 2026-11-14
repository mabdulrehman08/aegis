# Measured synthetic benchmark

Fixed seeds: [7, 19, 41, 83, 127]; Python 3.13.11; OR-Tools 9.15.6755.
Platform: macOS-26.7.2-arm64-arm-64bit-Mach-O; measured at 2026-11-14T04:14:39.014262+00:00.

One measured call per input/solver; first OR-Tools call includes cold import. No warmup or averaging.

| Scene | Phase | Solver / engine | New / existing | Unassigned | New distance (m) | Violations | Solver ms | Wall ms |
|---|---|---|---:|---:|---:|---:|---:|---:|
| grid3 | initial | nearest / nearest | 2 / 0 | 1 | 9.000 | 0 | 0.900 | 0.911 |
| grid3 | initial | ortools / ortools | 2 / 0 | 1 | 5.000 | 0 | 154.975 | 154.992 |
| grid3 | replan | nearest / nearest | 0 / 1 | 2 | 0.000 | 0 | 0.045 | 0.050 |
| grid3 | replan | ortools / ortools | 0 / 1 | 2 | 0.000 | 0 | 0.038 | 0.041 |
| seed-7 | initial | nearest / nearest | 5 / 0 | 5 | 58.000 | 0 | 2.741 | 2.772 |
| seed-7 | initial | ortools / ortools | 6 / 0 | 4 | 70.000 | 0 | 7.820 | 7.858 |
| seed-7 | replan | nearest / nearest | 4 / 1 | 5 | 29.000 | 0 | 1.570 | 1.589 |
| seed-7 | replan | ortools / ortools | 4 / 1 | 5 | 47.000 | 0 | 4.457 | 4.480 |
| seed-19 | initial | nearest / nearest | 5 / 0 | 5 | 51.000 | 0 | 3.222 | 3.252 |
| seed-19 | initial | ortools / ortools | 6 / 0 | 4 | 66.000 | 0 | 8.463 | 8.503 |
| seed-19 | replan | nearest / nearest | 4 / 1 | 5 | 49.000 | 0 | 1.782 | 1.803 |
| seed-19 | replan | ortools / ortools | 4 / 1 | 5 | 49.000 | 0 | 4.435 | 4.458 |
| seed-41 | initial | nearest / nearest | 5 / 0 | 5 | 46.000 | 0 | 3.895 | 3.939 |
| seed-41 | initial | ortools / ortools | 6 / 0 | 4 | 94.000 | 0 | 9.543 | 9.578 |
| seed-41 | replan | nearest / nearest | 4 / 1 | 5 | 46.000 | 0 | 2.641 | 2.664 |
| seed-41 | replan | ortools / ortools | 4 / 1 | 5 | 53.000 | 0 | 5.249 | 5.277 |
| seed-83 | initial | nearest / nearest | 4 / 0 | 6 | 40.000 | 0 | 3.005 | 3.037 |
| seed-83 | initial | ortools / ortools | 6 / 0 | 4 | 90.000 | 0 | 9.117 | 9.151 |
| seed-83 | replan | nearest / nearest | 4 / 1 | 5 | 53.000 | 0 | 1.793 | 1.814 |
| seed-83 | replan | ortools / ortools | 4 / 1 | 5 | 53.000 | 0 | 4.946 | 4.978 |
| seed-127 | initial | nearest / nearest | 6 / 0 | 4 | 68.000 | 0 | 2.598 | 2.626 |
| seed-127 | initial | ortools / ortools | 6 / 0 | 4 | 66.000 | 0 | 7.629 | 7.664 |
| seed-127 | replan | nearest / nearest | 4 / 1 | 5 | 29.000 | 0 | 1.448 | 1.467 |
| seed-127 | replan | ortools / ortools | 4 / 1 | 5 | 31.000 | 0 | 3.574 | 3.596 |

Timing is measured, machine-dependent, and not deterministic. Both solvers receive identical feasibility inputs.
Replans preserve one historical approved assignment with a busy responder, revise the map, and take another responder offline.
Historical routes remain unchanged and are flagged for review when their map revision is old. They are not new dispatchable work.
Historical-route review warnings: 12.
Human assessments are fixed synthetic input; no clinical inference, real dispatch, or RL training occurs.
