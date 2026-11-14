# Backend — Prince

`app/api/` owns HTTP routing, `app/models.py` defines implemented v1 payloads,
and `app/services/` loads synthetic fixtures. Tests live in `tests/`.
Run commands from the repository root; see `../docs/LOCAL_SETUP.md`.

Only health and read-only demo retrieval are implemented. ML recommendations,
authentication, persistence, approval auditing, WebSockets, and dispatch are future
work. No model or CV output is a medical assessment or dispatch authorization.
