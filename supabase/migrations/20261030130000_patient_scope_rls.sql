-- Row-level security for patient data and consent-aware delegated access.
-- Purpose is a trusted top-level JWT claim (`purpose`); clients must not be
-- allowed to mint or edit this claim. The API will establish request context
-- and re-authorize on resume in the auth/middleware step.

begin;

create function public.request_purpose()
returns text
language sql
stable
security invoker
set search_path = ''
as $$
    select nullif(auth.jwt() ->> 'purpose', '')
$$;

create function public.is_patient_owner(p_subject_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1
        from public.patient p
        where p.patient_id = p_subject_id
          and p.auth_user_id = (select auth.uid())
    )
$$;

create function public.has_patient_access(
    p_subject_id uuid,
    p_required_scope text
)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select public.is_patient_owner(p_subject_id)
    or exists (
        select 1
        from public.consent_grant cg
        where cg.subject_id = p_subject_id
          and cg.grantee_id = (select auth.uid())
          and cg.purpose = (select public.request_purpose())
          and cg.scope @> array[p_required_scope]::text[]
          and cg.granted_at <= statement_timestamp()
          and (cg.expires_at is null or cg.expires_at > statement_timestamp())
          and cg.revoked_at is null
    )
$$;

create function public.has_encounter_access(
    p_encounter_id uuid,
    p_required_scope text
)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1
        from public.encounter e
        where e.encounter_id = p_encounter_id
          and public.has_patient_access(e.patient_id, p_required_scope)
    )
$$;

create function public.has_app_role(p_allowed_roles text[])
returns boolean
language sql
stable
security invoker
set search_path = ''
as $$
    select coalesce(
        (auth.jwt() -> 'app_metadata' ->> 'role') = any (p_allowed_roles),
        false
    )
$$;

create function public.revoke_consent_once()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if old.revoked_at is not null then
        raise exception 'revoked consent grants cannot be changed'
            using errcode = '55000';
    end if;
    if new.revoked_at is null then
        raise exception 'consent updates may only revoke a grant'
            using errcode = '55000';
    end if;
    return new;
end;
$$;

create trigger consent_revoke_once
before update on public.consent_grant
for each row execute function public.revoke_consent_once();

alter table public.patient enable row level security;
alter table public.consent_grant enable row level security;
alter table public.clinic enable row level security;
alter table public.specialty enable row level security;
alter table public.doctor enable row level security;
alter table public.slot enable row level security;
alter table public.appointment enable row level security;
alter table public.document enable row level security;
alter table public.document_chunk enable row level security;
alter table public.encounter enable row level security;
alter table public.clinical_note_version enable row level security;
alter table public.audit_event enable row level security;
alter table public.workflow_run enable row level security;

-- Authenticated users can read active operational catalog data, but cannot edit it.
create policy clinic_authenticated_read
    on public.clinic for select to authenticated
    using (is_active);

create policy specialty_authenticated_read
    on public.specialty for select to authenticated
    using (is_active);

create policy doctor_authenticated_read
    on public.doctor for select to authenticated
    using (is_active);

create policy slot_authenticated_read
    on public.slot for select to authenticated
    using (
        starts_at > statement_timestamp()
        and reserved_count < capacity
        and exists (
            select 1
            from public.doctor d
            join public.clinic c on c.clinic_id = d.clinic_id
            where d.doctor_id = slot.doctor_id
              and d.clinic_id = slot.clinic_id
              and d.is_active
              and c.is_active
        )
    );

create policy patient_scoped_read
    on public.patient for select to authenticated
    using (public.has_patient_access(patient_id, 'patient:read'));

create policy consent_subject_or_grantee_read
    on public.consent_grant for select to authenticated
    using (
        public.is_patient_owner(subject_id)
        or grantee_id = (select auth.uid())
    );

create policy consent_patient_create
    on public.consent_grant for insert to authenticated
    with check (
        public.is_patient_owner(subject_id)
        and grantee_id is distinct from (select auth.uid())
        and revoked_at is null
        and granted_at <= statement_timestamp()
    );

create policy consent_patient_revoke
    on public.consent_grant for update to authenticated
    using (
        revoked_at is null
        and public.is_patient_owner(subject_id)
    )
    with check (
        revoked_at is not null
        and public.is_patient_owner(subject_id)
    );

create policy appointment_patient_read
    on public.appointment for select to authenticated
    using (public.has_patient_access(patient_id, 'appointments:read'));

create policy document_patient_read
    on public.document for select to authenticated
    using (public.has_patient_access(patient_id, 'records:read'));

create policy document_chunk_patient_read
    on public.document_chunk for select to authenticated
    using (public.has_patient_access(patient_id, 'records:read'));

create policy encounter_patient_read
    on public.encounter for select to authenticated
    using (public.has_patient_access(patient_id, 'encounters:read'));

create policy clinical_note_patient_read
    on public.clinical_note_version for select to authenticated
    using (public.has_encounter_access(encounter_id, 'scribe:read'));

create policy workflow_subject_read
    on public.workflow_run for select to authenticated
    using (
        subject_id is not null
        and public.has_patient_access(subject_id, 'workflow:read')
    );

create policy audit_actor_or_compliance_read
    on public.audit_event for select to authenticated
    using (
        actor_id = (select auth.uid())
        or public.has_app_role(array['compliance', 'security_admin']::text[])
    );

-- Grant only the reads and consent operations for which policies exist.
grant select on
    public.consent_grant,
    public.appointment,
    public.document,
    public.document_chunk,
    public.encounter,
    public.clinical_note_version,
    public.audit_event,
    public.workflow_run
to authenticated;

grant select (patient_id, language_tag, created_at, updated_at)
    on public.patient to authenticated;
grant select (clinic_id, site_code, name, timezone, is_active, created_at)
    on public.clinic to authenticated;
grant select (specialty_id, code, display_name, is_active, created_at)
    on public.specialty to authenticated;
grant select (doctor_id, clinic_id, specialty_id, display_name, registry_status, language_tags, is_active, created_at)
    on public.doctor to authenticated;
grant select (slot_id, doctor_id, clinic_id, starts_at, ends_at, capacity, reserved_count, version, created_at, updated_at)
    on public.slot to authenticated;

grant insert on public.consent_grant to authenticated;
grant update (revoked_at) on public.consent_grant to authenticated;
grant usage on schema extensions to authenticated;

revoke all on function public.request_purpose() from public, anon;
revoke all on function public.is_patient_owner(uuid) from public, anon;
revoke all on function public.has_patient_access(uuid, text) from public, anon;
revoke all on function public.has_encounter_access(uuid, text) from public, anon;
revoke all on function public.has_app_role(text[]) from public, anon;
grant execute on function public.request_purpose() to authenticated, service_role;
grant execute on function public.is_patient_owner(uuid) to authenticated, service_role;
grant execute on function public.has_patient_access(uuid, text) to authenticated, service_role;
grant execute on function public.has_encounter_access(uuid, text) to authenticated, service_role;
grant execute on function public.has_app_role(text[]) to authenticated, service_role;

revoke all on function public.revoke_consent_once() from public, anon, authenticated;

commit;
