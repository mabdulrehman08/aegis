# AEGIS

HackPrinceton 2026 prototype for human-supervised emergency response coordination.
This foundation is a local development scaffold, **not a clinical or production
emergency-response system**. All included people and coordinates are synthetic.

## Repository layout and ownership

| Directory | Responsibility | Owner |
| --- | --- | --- |
| `backend/` | FastAPI, future ML/recommendations and coordination | Prince |
| `cv/` | Visual observations and spatial mapping; no medical severity inference | AskAr |
| `hardware/` | Device integration and responder telemetry | Muhammad |
| `frontend/` | Operator UI and explicit human dispatch approval | Hillary |
| `shared/` | Versioned integration contract and synthetic scenario fixtures | All four |
| `docs/` | Local setup and architecture decisions | All four |

`shared/CONTRACT.md` is the integration source of truth. Propose contract changes
in a PR and have affected module owners review them before implementation.
The module READMEs identify boundaries; no CV, hardware, UI, optimization, or
dispatch features are implemented by this foundation.

## Quick start

Install Python 3.11+ and `uv`, then run from the repository root:

```sh
uv sync --locked
uv run uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

In another terminal:

```sh
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/v1/scenarios/demo
uv run pytest
```

Interactive API docs: `http://127.0.0.1:8000/docs`.
See `docs/LOCAL_SETUP.md` for a standard-library venv alternative.

## Safety boundaries

- CV reports observations and coordinates, never medical severity or triage.
- Medical assessments, when added, require an identified human assessor.
- Future recommendations are advisory. Dispatch requires explicit, authenticated
  human approval of the exact proposal revision; it must never execute implicitly.
- There is no dispatch endpoint or device command path in this scaffold. Reserved
  interfaces in the contract are **not implemented**.
- Bind only to loopback: the scaffold has no authentication or durable storage.

## Development workflow

Use feature branches and review PRs before merging. Keep changes inside your
module when possible; coordinate shared-contract changes with affected owners.
Run `uv run pytest` before review. Commit `uv.lock` with dependency changes.
Do not commit credentials, real casualty data, or captured imagery.
