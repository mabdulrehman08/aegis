# Local development

## Prerequisites

- Python 3.11 or newer; `.python-version` selects Python 3.13 for the uv workflow.
- `uv` for the shared locked dependency workflow, or Python's built-in `venv`.
- Git; GitHub CLI is optional for opening review PRs.
- No API keys, devices, model weights, database, or frontend toolchain required.

## Recommended workflow

Run from the repository root:

```sh
uv sync --locked
uv run uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

Use a second terminal at the same root:

```sh
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/api/v1/scenarios/demo
uv run pytest
```

Health returns `{"status":"ok","service":"aegis-backend","version":"0.1.0"}`.
The demo returns exactly three synthetic casualties and two synthetic responders.
All casualties start unassessed. Health is process liveness, not a claim that
external devices, clinical systems, or future dependencies are ready.

Open `http://127.0.0.1:8000/docs` or `/openapi.json` for the implemented API.
There is no environment configuration required. CORS is intentionally not enabled;
when the frontend exists, agree an explicit local-origin allowlist or proxy.

## Without uv

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install 'fastapi>=0.115,<1' 'pydantic>=2.10,<3' 'uvicorn>=0.30,<1' 'httpx2>=2,<3' 'pytest>=8,<10'
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
python -m pytest
```

This fallback does not use `uv.lock`; prefer `uv sync --locked` for reproducibility.
On Windows activate with `.venv\Scripts\Activate.ps1` instead of `source`.

## Troubleshooting and boundaries

- Import errors: run from the repository root, not `backend/`.
- Port in use: choose `--port 8001` and change the URLs accordingly.
- Fixture errors: validate `shared/mock/scenario.demo.json` against the contract;
  malformed data must fail rather than silently inventing casualty information.
- Stop the server with Ctrl-C. The fixture is read-only; no state is written.
- Do not expose the server publicly. Authentication, authorization, persistence,
  audit records, and safe dispatch gates are not implemented.
