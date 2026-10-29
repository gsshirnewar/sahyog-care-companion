-- Sahyog Care Companion foundation schema.
-- Synthetic-only demonstrator. Patient-level RLS policies are delivered in the
-- following security step; API roles are denied table access until then.

begin;

create schema if not exists extensions;
create extension if not exists vector with schema extensions;
grant usage on schema extensions to service_role;

create type public.appointment_status as enum (
    'proposed',
    'reserved',
    'cancelled',
    'completed',
    'no_show'
);

create type public.document_upload_status as enum (
    'pending',
    'processing',
    'ready',
    'needs_review',
    'failed',
    'deleted'
);

create type public.document_deletion_status as enum (
    'active',
    'scheduled',
    'deleting',
    'deleted',
    'failed'
);

create type public.encounter_status as enum (
    'open',
    'in_review',
    'signed',
    'closed',
    'cancelled'
);

create type public.workflow_status as enum (
    'running',
    'paused',
    'completed',
    'failed',
    'expired',
    'cancelled'
);

create table public.patient (
    patient_id uuid primary key default gen_random_uuid(),
    auth_user_id uuid unique references auth.users(id) on delete restrict,
    identity_ref text not null unique,
    language_tag text not null default 'en',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint patient_identity_ref_nonempty check (length(btrim(identity_ref)) > 0),
    constraint patient_language_tag_nonempty check (length(btrim(language_tag)) > 0)
);

create table public.consent_grant (
    grant_id uuid primary key default gen_random_uuid(),
    subject_id uuid not null references public.patient(patient_id) on delete restrict,
    grantee_id uuid references auth.users(id) on delete restrict,
    purpose text not null,
    scope text[] not null,
    resource_type text not null,
    granted_at timestamptz not null default now(),
    expires_at timestamptz,
    revoked_at timestamptz,
    evidence_ref text,
    version integer not null default 1,
    created_at timestamptz not null default now(),
    constraint consent_purpose_nonempty check (length(btrim(purpose)) > 0),
    constraint consent_resource_type_nonempty check (length(btrim(resource_type)) > 0),
    constraint consent_scope_nonempty check (cardinality(scope) > 0),
    constraint consent_scope_values_nonempty check (
        array_position(scope, '') is null
        and array_position(scope, null) is null
    ),
    constraint consent_expiry_after_grant check (expires_at is null or expires_at > granted_at),
    constraint consent_revocation_after_grant check (revoked_at is null or revoked_at >= granted_at),
    constraint consent_version_positive check (version > 0)
);

create index consent_subject_purpose_idx
    on public.consent_grant (subject_id, purpose, granted_at desc);
create index consent_grantee_idx
    on public.consent_grant (grantee_id) where grantee_id is not null;

create table public.clinic (
    clinic_id uuid primary key default gen_random_uuid(),
    site_code text not null unique,
    name text not null,
    timezone text not null default 'Asia/Kolkata',
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    constraint clinic_site_code_nonempty check (length(btrim(site_code)) > 0),
    constraint clinic_name_nonempty check (length(btrim(name)) > 0),
    constraint clinic_timezone_nonempty check (length(btrim(timezone)) > 0)
);

create table public.specialty (
    specialty_id uuid primary key default gen_random_uuid(),
    code text not null unique,
    display_name text not null,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    constraint specialty_code_nonempty check (length(btrim(code)) > 0),
    constraint specialty_display_name_nonempty check (length(btrim(display_name)) > 0)
);

create table public.doctor (
    doctor_id uuid primary key default gen_random_uuid(),
    clinic_id uuid not null references public.clinic(clinic_id) on delete restrict,
    specialty_id uuid not null references public.specialty(specialty_id) on delete restrict,
    auth_user_id uuid references auth.users(id) on delete restrict,
    display_name text not null,
    registry_status text not null default 'unverified',
    language_tags text[] not null default array['en']::text[],
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    constraint doctor_display_name_nonempty check (length(btrim(display_name)) > 0),
    constraint doctor_registry_status_nonempty check (length(btrim(registry_status)) > 0),
    constraint doctor_languages_nonempty check (cardinality(language_tags) > 0),
    constraint doctor_clinic_pair_unique unique (doctor_id, clinic_id)
);

create index doctor_clinic_specialty_active_idx
    on public.doctor (clinic_id, specialty_id) where is_active;
create index doctor_auth_user_idx
    on public.doctor (auth_user_id) where auth_user_id is not null;

create table public.slot (
    slot_id uuid primary key default gen_random_uuid(),
    doctor_id uuid not null,
    clinic_id uuid not null references public.clinic(clinic_id) on delete restrict,
    starts_at timestamptz not null,
    ends_at timestamptz not null,
    capacity integer not null default 1,
    reserved_count integer not null default 0,
    version bigint not null default 1,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint slot_valid_time_range check (ends_at > starts_at),
    constraint slot_capacity_positive check (capacity > 0),
    constraint slot_reserved_within_capacity check (reserved_count >= 0 and reserved_count <= capacity),
    constraint slot_version_positive check (version > 0),
    constraint slot_doctor_clinic_fk
        foreign key (doctor_id, clinic_id)
        references public.doctor(doctor_id, clinic_id)
        on delete restrict,
    constraint slot_doctor_clinic_time_unique unique (doctor_id, clinic_id, starts_at, ends_at)
);

create index slot_availability_idx
    on public.slot (clinic_id, starts_at)
    where reserved_count < capacity;
create index slot_doctor_start_idx on public.slot (doctor_id, starts_at);

create table public.appointment (
    appointment_id uuid primary key default gen_random_uuid(),
    patient_id uuid not null references public.patient(patient_id) on delete restrict,
    slot_id uuid not null references public.slot(slot_id) on delete restrict,
    status public.appointment_status not null default 'proposed',
    workflow text not null default 'w1_booking',
    idempotency_key text not null,
    created_by uuid not null references auth.users(id) on delete restrict,
    source text not null default 'local_demo',
    his_reconciliation_state text not null default 'not_applicable',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint appointment_idempotency_unique unique (created_by, workflow, idempotency_key),
    constraint appointment_idempotency_key_nonempty check (
        length(btrim(idempotency_key)) between 1 and 255
    ),
    constraint appointment_workflow_nonempty check (length(btrim(workflow)) > 0),
    constraint appointment_source_nonempty check (length(btrim(source)) > 0),
    constraint appointment_his_state_nonempty check (length(btrim(his_reconciliation_state)) > 0),
    constraint appointment_id_patient_unique unique (appointment_id, patient_id)
);

create index appointment_patient_created_idx
    on public.appointment (patient_id, created_at desc);
create index appointment_slot_status_idx
    on public.appointment (slot_id, status);

create table public.document (
    doc_id uuid primary key default gen_random_uuid(),
    patient_id uuid not null references public.patient(patient_id) on delete restrict,
    document_type text not null,
    object_key text not null unique,
    upload_status public.document_upload_status not null default 'pending',
    sensitivity text not null default 'health_sensitive',
    content_hash text,
    page_count integer,
    uploaded_at timestamptz not null default now(),
    retention_until timestamptz,
    deletion_status public.document_deletion_status not null default 'active',
    deleted_at timestamptz,
    created_at timestamptz not null default now(),
    constraint document_object_key_nonempty check (length(btrim(object_key)) > 0),
    constraint document_type_nonempty check (length(btrim(document_type)) > 0),
    constraint document_sensitivity_nonempty check (length(btrim(sensitivity)) > 0),
    constraint document_page_count_positive check (page_count is null or page_count > 0),
    constraint document_retention_after_upload check (retention_until is null or retention_until > uploaded_at),
    constraint document_deleted_state_consistent check (
        (deletion_status = 'deleted' and deleted_at is not null)
        or (deletion_status <> 'deleted' and deleted_at is null)
    ),
    constraint document_id_patient_unique unique (doc_id, patient_id)
);

create index document_patient_uploaded_idx
    on public.document (patient_id, uploaded_at desc);
create index document_retention_idx
    on public.document (retention_until)
    where deletion_status in ('scheduled', 'failed');

create table public.document_chunk (
    chunk_id uuid primary key default gen_random_uuid(),
    doc_id uuid not null,
    patient_id uuid not null,
    page_number integer not null,
    section text,
    start_offset integer,
    end_offset integer,
    text_content text not null,
    embedding extensions.vector(1024),
    extraction_version text not null,
    created_at timestamptz not null default now(),
    constraint chunk_document_patient_fk
        foreign key (doc_id, patient_id)
        references public.document(doc_id, patient_id)
        on delete cascade,
    constraint chunk_page_positive check (page_number > 0),
    constraint chunk_offsets_valid check (
        (start_offset is null and end_offset is null)
        or (start_offset is not null and end_offset is not null and start_offset >= 0 and end_offset >= start_offset)
    ),
    constraint chunk_text_nonempty check (length(btrim(text_content)) > 0),
    constraint chunk_extraction_version_nonempty check (length(btrim(extraction_version)) > 0)
);

create index document_chunk_patient_doc_idx
    on public.document_chunk (patient_id, doc_id, page_number);
create index document_chunk_embedding_hnsw_idx
    on public.document_chunk using hnsw (embedding extensions.vector_cosine_ops)
    where embedding is not null;

create table public.encounter (
    encounter_id uuid primary key default gen_random_uuid(),
    appointment_id uuid unique,
    patient_id uuid not null references public.patient(patient_id) on delete restrict,
    audio_object_key text,
    transcript_ref text,
    status public.encounter_status not null default 'open',
    started_at timestamptz not null default now(),
    retention_until timestamptz,
    audio_deletion_status public.document_deletion_status not null default 'active',
    audio_deleted_at timestamptz,
    created_at timestamptz not null default now(),
    constraint encounter_retention_after_start check (retention_until is null or retention_until > started_at),
    constraint encounter_audio_deleted_state_consistent check (
        (audio_deletion_status = 'deleted' and audio_deleted_at is not null)
        or (audio_deletion_status <> 'deleted' and audio_deleted_at is null)
    ),
    constraint encounter_appointment_patient_fk
        foreign key (appointment_id, patient_id)
        references public.appointment(appointment_id, patient_id)
        on delete restrict
);

create index encounter_patient_started_idx
    on public.encounter (patient_id, started_at desc);
create index encounter_audio_retention_idx
    on public.encounter (retention_until)
    where audio_object_key is not null and audio_deletion_status in ('scheduled', 'failed');

create table public.clinical_note_version (
    note_id uuid not null default gen_random_uuid(),
    encounter_id uuid not null references public.encounter(encounter_id) on delete restrict,
    version integer not null,
    draft_json jsonb not null,
    signed_by uuid references auth.users(id) on delete restrict,
    signed_at timestamptz,
    content_hash text,
    prior_version integer,
    created_at timestamptz not null default now(),
    primary key (note_id, version),
    constraint clinical_note_version_positive check (version > 0),
    constraint clinical_note_prior_version_valid check (prior_version is null or (prior_version > 0 and prior_version < version)),
    constraint clinical_note_signed_fields_consistent check (
        (signed_at is null and signed_by is null and content_hash is null)
        or (signed_at is not null and signed_by is not null and content_hash is not null and length(btrim(content_hash)) > 0)
    ),
    constraint clinical_note_prior_version_fk
        foreign key (note_id, prior_version)
        references public.clinical_note_version(note_id, version)
        deferrable initially deferred
);

create index clinical_note_encounter_version_idx
    on public.clinical_note_version (encounter_id, version desc);
create index clinical_note_signed_idx
    on public.clinical_note_version (signed_at desc)
    where signed_at is not null;

create function public.reject_signed_note_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if old.signed_at is not null then
        raise exception 'signed clinical note versions are immutable'
            using errcode = '55000';
    end if;
    if tg_op = 'DELETE' then
        return old;
    end if;
    return new;
end;
$$;

create trigger clinical_note_signed_immutable
before update or delete on public.clinical_note_version
for each row execute function public.reject_signed_note_mutation();

create table public.audit_event (
    event_id uuid primary key default gen_random_uuid(),
    actor_id uuid references auth.users(id) on delete restrict,
    actor_role text not null,
    purpose text not null,
    action text not null,
    resource_type text not null,
    resource_id uuid,
    decision text not null,
    occurred_at timestamptz not null default now(),
    trace_id text not null,
    consent_id uuid references public.consent_grant(grant_id) on delete restrict,
    constraint audit_actor_role_nonempty check (length(btrim(actor_role)) > 0),
    constraint audit_purpose_nonempty check (length(btrim(purpose)) > 0),
    constraint audit_action_nonempty check (length(btrim(action)) > 0),
    constraint audit_resource_type_nonempty check (length(btrim(resource_type)) > 0),
    constraint audit_decision_nonempty check (length(btrim(decision)) > 0),
    constraint audit_trace_id_nonempty check (length(btrim(trace_id)) > 0)
);

create index audit_event_occurred_idx on public.audit_event (occurred_at desc);
create index audit_event_actor_occurred_idx on public.audit_event (actor_id, occurred_at desc);
create index audit_event_resource_idx
    on public.audit_event (resource_type, resource_id, occurred_at desc);
create index audit_event_trace_idx on public.audit_event (trace_id);

create function public.reject_audit_event_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    raise exception 'audit events are append-only'
        using errcode = '55000';
end;
$$;

create trigger audit_event_append_only
before update or delete on public.audit_event
for each row execute function public.reject_audit_event_mutation();

create table public.workflow_run (
    run_id uuid primary key default gen_random_uuid(),
    workflow text not null,
    subject_id uuid references public.patient(patient_id) on delete restrict,
    state_ref text not null,
    interrupt_type text,
    expires_at timestamptz,
    status public.workflow_status not null default 'running',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint workflow_run_name_nonempty check (length(btrim(workflow)) > 0),
    constraint workflow_state_ref_nonempty check (length(btrim(state_ref)) > 0),
    constraint workflow_interrupt_nonempty check (interrupt_type is null or length(btrim(interrupt_type)) > 0),
    constraint workflow_expiry_after_creation check (expires_at is null or expires_at > created_at)
);

create index workflow_run_subject_created_idx
    on public.workflow_run (subject_id, created_at desc);
create index workflow_run_status_expiry_idx
    on public.workflow_run (status, expires_at)
    where status in ('running', 'paused');

-- Do not expose protected tables through PostgREST before the scoped RLS step.
revoke all on table
    public.patient,
    public.consent_grant,
    public.clinic,
    public.specialty,
    public.doctor,
    public.slot,
    public.appointment,
    public.document,
    public.document_chunk,
    public.encounter,
    public.clinical_note_version,
    public.audit_event,
    public.workflow_run
from public, anon, authenticated;

grant all on table
    public.patient,
    public.consent_grant,
    public.clinic,
    public.specialty,
    public.doctor,
    public.slot,
    public.appointment,
    public.document,
    public.document_chunk,
    public.encounter,
    public.clinical_note_version,
    public.audit_event,
    public.workflow_run
to service_role;

revoke all on function public.reject_signed_note_mutation() from public, anon, authenticated;
revoke all on function public.reject_audit_event_mutation() from public, anon, authenticated;

commit;
