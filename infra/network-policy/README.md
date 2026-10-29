# Local egress boundary

The local Compose network is marked `internal: true`. API and worker containers can
communicate with Redis and each other, but have no direct internet route. The API
port is bound to `127.0.0.1` only. Local Supabase runs separately through the
Supabase CLI and is reached through its loopback-published host port using
`host.docker.internal:54321`.

This is a local-development control, not a production firewall or a guarantee
about host/Docker Desktop routing. Do not add general-purpose network attachment,
proxy credentials, real patient data, or external model endpoints without an
approved egress policy and an explicit allowlist. Verify network behavior on the
developer's Docker platform before relying on it as a security control.