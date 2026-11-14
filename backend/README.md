# Backend — Prince

`app/api/` owns HTTP routing, `app/models.py` defines v1 validation schemas,
and `app/services/` loads synthetic fixtures. Tests live in `tests/`.
Run commands from the repository root; see `../docs/LOCAL_SETUP.md`.

Only health and read-only demo retrieval are implemented. ML recommendations,
authentication, persistence, approval auditing, WebSockets, and dispatch are future
work. No model or CV output is a medical assessment or dispatch authorization.
Reserved tracking, map, route, proposal metadata, and device-event models validate
payload shape only; they do not enable those features or real dispatch.
