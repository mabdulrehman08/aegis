# Advisory explanations (optional, simulation only)

The deterministic explanation service converts typed optimizer output into facts
with `human_confirmed`, `estimated`, `unknown`, and `procedural` provenance. It
never changes assessments, constraints, approvals, or dispatch state. Detector
confidence is not clinical certainty. A planned distance is not a measured ETA.

No credentials or external service are required. `explain(explanation_input(scene,
result))` returns verified template facts. The optimizer's structured reasons and
rationale remain available even without this presentation layer.

For an optional LLM, point `AEGIS_ADVISORY_URL` at a JSON gateway and optionally
set `AEGIS_ADVISORY_TOKEN` in your local environment (never commit it). No particular
vendor SDK is required. The gateway receives `instruction` and `input.facts`, and
must return exactly `{"fact_ids": ["known-id", "another-known-id"]}`. It may rank
supplied facts, not write free-form diagnoses or commands. All omitted facts are
restored, so uncertainty and approval requirements cannot disappear. Invalid IDs,
extra output fields, duplicate IDs, timeouts, and connection failures fall back
to deterministic output. The timeout is two seconds and response size is bounded.
You can copy `.env.example` to `.env` and start with `uv run --env-file .env uvicorn
backend.app.main:app --host 127.0.0.1 --port 8000`; blank values keep the provider
disabled. Normal startup does not require an environment file.

The gateway is an optional adapter, **not a bundled hosted LLM integration**.
Enabling it sends synthetic explanatory facts to the configured operator-trusted
endpoint. Keep it disabled unless that endpoint is trusted. It cannot dispatch.
