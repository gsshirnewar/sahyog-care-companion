# Sahyog Care Companion — synthetic foundation prototype

Synthetic-only demonstrator code aligned to the implementation guide. This is not a clinically validated or production-ready care system. Do not use real patient information or rely on outputs for clinical decisions.

## Setup

Requirements: Python 3.12+, Docker Compose v2, and Supabase CLI.

1. Copy `infra/env/local/.env.example` to `infra/env/local/.env` and use only local demo credentials.
2. Start local Supabase from this repository root with `supabase start`.
3. Apply migrations with `supabase migration up` and run the database RLS suite with `supabase test db`.
4. Install Python dependencies with `python -m pip install -r services/api/requirements.txt`.
5. Run the application containers with `docker compose --env-file infra/env/local/.env -f infra/compose/compose.yaml up --build`.
6. Run Python regressions with `python -m pytest -q`.

## Implemented code and gates

- Supabase migrations define the initial data model and RLS boundary. Database migrations and SQL tests must be run against local Supabase before relying on them.
- API auth verifies Supabase JWT signatures, issuer, audience, expiry, server-managed role claims, and request correlation metadata.
- Domain code provides consent-aware access decisions, append-only audit record contracts, fail-closed red-flag gating, specialty crosswalk validation, atomic scheduling operations, citation verification, and verified audio deletion.
- W1 uses a LangGraph red-flag → specialty → slot-search graph. Red-flag rules and specialty mappings remain disabled unless the clinical owner approves them. Until configured, intake routes to human review.
- W2 implements bounded PDF text extraction and extractive patient-scoped Q&A contracts. OCR, asynchronous ingestion, durable storage, and embedding retrieval require their adapters.
- W3 implements synthetic-transcript SOAP drafts, source-span checks, clinician approval, immutable signed versions, and rejection/edit paths. Audio capture/STT is not enabled.

The API workflow service dependencies intentionally return `503` until deployment wiring supplies persistent Supabase repositories, live consent/ownership checks, object storage, audit persistence, and durable workflow state. Tests inject synthetic repositories to exercise the API/service integration; they do not prove a running database integration. Do not replace these missing adapters with unrestricted in-memory state for patient use.

## Regression evidence

Python tests cover auth and role mapping, API middleware, deterministic domain behavior, W1 graph and endpoints, W2 upload/Q&A and citations, and W3 review/sign/versioning. The SQL RLS tests cover patient isolation and consent states, but need the Supabase CLI to execute. The golden sets are placeholders and explicitly await clinical-owner approval; they are not medical guidance.
