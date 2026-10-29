# Local foundation bootstrap

This bootstrap is synthetic-only. It starts Supabase locally with the Supabase
CLI and runs the API, Redis, and a no-op worker in Docker Compose. It does not
include clinical workflows, schema migrations, production credentials, or an
external model integration.

## Prerequisites

- Docker Desktop with Compose v2
- Supabase CLI
- Python is not required on the host

## Start

From the repository root, copy `infra/env/local/.env.example` to
`infra/env/local/.env`; keep the local-only values and do not use production
secrets. Start the local Supabase stack with `supabase start`, then run Compose
with `docker compose --env-file infra/env/local/.env -f infra/compose/compose.yaml up --build`.

The API is available at `http://127.0.0.1:8000/health/ready`. Check local
Supabase credentials with `supabase status`; only the anon key belongs in this
local application template. Never pass the service-role key to the API or
frontend.

Stop with `docker compose -f infra/compose/compose.yaml down`. Add `-v` only when
you intentionally want to delete the local Redis volume. Supabase data is
managed separately by the Supabase CLI.

## Safety notes

- Use synthetic fixtures only. Do not upload real patient records, audio, or
  identifiers into this demonstrator.
- The Compose network is isolated from direct internet egress by default; see
  `infra/network-policy/README.md` for platform caveats.
- The API readiness endpoint is currently a bootstrap health check, not a
  database, authentication, or clinical-readiness assertion.