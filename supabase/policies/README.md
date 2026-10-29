# Supabase RLS policies

The executable policies are versioned with the database migrations:

- `../migrations/20261030130000_patient_scope_rls.sql` enables RLS and applies patient, consent, catalog, workflow, and audit policies.
- `../tests/rls/patient_scope.test.sql` exercises patient isolation, active/expired/revoked consent, purpose/scope checks, compliance audit visibility, and immutable signed notes.

## Trusted request-purpose claim

Delegated access requires the signed JWT to contain a top-level `purpose` claim (for example, `records` or `booking`). Missing purpose denies delegated reads. Configure it through a trusted Supabase Auth access-token hook or another server-controlled JWT issuer; never accept it from user-editable metadata or a client request body. Owner access is tied to `patient.auth_user_id = auth.uid()` and does not rely on this claim.

The `service_role` bypasses RLS by design. It must remain server-only, and API code using it must independently build and enforce the authenticated request context, consent checks, audit writes, and patient scope. Client applications must use the anon key plus an end-user JWT.

Run the SQL regression suite with `supabase test db` from the repository root after starting the local Supabase stack. Tests use synthetic fixtures and roll their changes back.
