# Measured synthetic benchmark

Fixed seeds: [7, 19, 41, 83, 127]; Python 3.13.11; OR-Tools 9.15.6755.
Platform: macOS-26.7.2-arm64-arm-64bit-Mach-O; measured at 2026-11-14T04:11:40.534784+00:00.

One measured call per input/solver; first OR-Tools call includes cold import. No warmup or averaging.

| Scene | Phase | Solver / engine | New / existing | Unassigned | New distance (m) | Violations | Solver ms | Wall ms |
|---|---|---|---:|---:|---:|---:|---:|---:|
| grid3 | initial | nearest / nearest | 2 / 0 | 1 | 9.000 | 0 | 0.970 | 0.981 |
| grid3 | initial | ortools / ortools | 2 / 0 | 1 | 5.000 | 0 | 162.131 | 162.153 |
| grid3 | replan | nearest / nearest | 0 / 1 | 2 | 0.000 | 0 | 0.049 | 0.054 |
| grid3 | replan | ortools / ortools | 0 / 1 | 2 | 0.000 | 0 | 0.042 | 0.046 |
| seed-7 | initial | nearest / nearest | 5 / 0 | 5 | 58.000 | 0 | 2.941 | 2.972 |
| seed-7 | initial | ortools / ortools | 6 / 0 | 4 | 70.000 | 0 | 9.341 | 9.378 |
| seed-7 | replan | nearest / nearest | 4 / 1 | 5 | 29.000 | 0 | 1.623 | 1.642 |
| seed-7 | replan | ortools / ortools | 4 / 1 | 5 | 47.000 | 0 | 6.097 | 6.137 |
| seed-19 | initial | nearest / nearest | 5 / 0 | 5 | 51.000 | 0 | 4.435 | 4.477 |
| seed-19 | initial | ortools / ortools | 6 / 0 | 4 | 66.000 | 0 | 9.327 | 9.374 |
| seed-19 | replan | nearest / nearest | 4 / 1 | 5 | 49.000 | 0 | 2.132 | 2.156 |
| seed-19 | replan | ortools / ortools | 4 / 1 | 5 | 49.000 | 0 | 5.125 | 5.171 |
| seed-41 | initial | nearest / nearest | 5 / 0 | 5 | 46.000 | 0 | 4.170 | 4.212 |
| seed-41 | initial | ortools / ortools | 6 / 0 | 4 | 94.000 | 0 | 11.901 | 11.972 |
| seed-41 | replan | nearest / nearest | 4 / 1 | 5 | 46.000 | 0 | 2.817 | 2.846 |
| seed-41 | replan | ortools / ortools | 4 / 1 | 5 | 53.000 | 0 | 5.722 | 5.759 |
| seed-83 | initial | nearest / nearest | 4 / 0 | 6 | 40.000 | 0 | 3.227 | 3.265 |
| seed-83 | initial | ortools / ortools | 6 / 0 | 4 | 90.000 | 0 | 10.419 | 10.483 |
| seed-83 | replan | nearest / nearest | 4 / 1 | 5 | 53.000 | 0 | 1.857 | 1.879 |
| seed-83 | replan | ortools / ortools | 4 / 1 | 5 | 53.000 | 0 | 5.776 | 5.822 |
| seed-127 | initial | nearest / nearest | 6 / 0 | 4 | 68.000 | 0 | 2.779 | 2.812 |
| seed-127 | initial | ortools / ortools | 6 / 0 | 4 | 66.000 | 0 | 8.609 | 8.669 |
| seed-127 | replan | nearest / nearest | 4 / 1 | 5 | 29.000 | 0 | 1.585 | 1.612 |
| seed-127 | replan | ortools / ortools | 4 / 1 | 5 | 31.000 | 0 | 4.146 | 4.179 |

Timing is measured, machine-dependent, and not deterministic. Both solvers receive identical feasibility inputs.
Replans preserve one historical approved assignment with a busy responder, revise the map, and take another responder offline.
Historical routes remain unchanged and are flagged for review when their map revision is old. They are not new dispatchable work.
Historical-route review warnings: 12.
Human assessments are fixed synthetic input; no clinical inference, real dispatch, or RL training occurs.
