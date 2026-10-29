-- Synthetic RLS leak and consent lifecycle tests. Run with `supabase test db`.
-- The transaction rolls all fixtures back after assertions complete.
begin;

insert into auth.users (id, aud, role, email, raw_app_meta_data, raw_user_meta_data)
values
    ('10000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', 'patient-a@example.test', '{"provider":"email","providers":["email"]}', '{}'),
    ('10000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated', 'patient-b@example.test', '{"provider":"email","providers":["email"]}', '{}'),
    ('10000000-0000-4000-8000-000000000003', 'authenticated', 'authenticated', 'delegate-active@example.test', '{"provider":"email","providers":["email"]}', '{}'),
    ('10000000-0000-4000-8000-000000000004', 'authenticated', 'authenticated', 'delegate-expired@example.test', '{"provider":"email","providers":["email"]}', '{}'),
    ('10000000-0000-4000-8000-000000000005', 'authenticated', 'authenticated', 'delegate-revoked@example.test', '{"provider":"email","providers":["email"]}', '{}'),
    ('10000000-0000-4000-8000-000000000006', 'authenticated', 'authenticated', 'delegate-wrong-purpose@example.test', '{"provider":"email","providers":["email"]}', '{}'),
    ('10000000-0000-4000-8000-000000000007', 'authenticated', 'authenticated', 'delegate-wrong-scope@example.test', '{"provider":"email","providers":["email"]}', '{}'),
    ('10000000-0000-4000-8000-000000000008', 'authenticated', 'authenticated', 'compliance@example.test', '{"provider":"email","providers":["email"],"role":"compliance"}', '{}');

insert into public.patient (patient_id, auth_user_id, identity_ref, language_tag)
values
    ('20000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001', 'synthetic-patient-a', 'en'),
    ('20000000-0000-4000-8000-000000000002', '10000000-0000-4000-8000-000000000002', 'synthetic-patient-b', 'en');

insert into public.consent_grant (
    grant_id, subject_id, grantee_id, purpose, scope, resource_type,
    granted_at, expires_at, revoked_at
)
values
    ('30000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000003', 'records', array['records:read'], 'document', now() - interval '1 day', now() + interval '1 day', null),
    ('30000000-0000-4000-8000-000000000002', '20000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000004', 'records', array['records:read'], 'document', now() - interval '2 days', now() - interval '1 day', null),
    ('30000000-0000-4000-8000-000000000003', '20000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000005', 'records', array['records:read'], 'document', now() - interval '2 days', now() + interval '1 day', now() - interval '1 day'),
    ('30000000-0000-4000-8000-000000000004', '20000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000006', 'records', array['records:read'], 'document', now() - interval '1 day', now() + interval '1 day', null),
    ('30000000-0000-4000-8000-000000000005', '20000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000007', 'records', array['appointments:read'], 'appointment', now() - interval '1 day', now() + interval '1 day', null);

insert into public.document (doc_id, patient_id, document_type, object_key, upload_status)
values
    ('40000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000001', 'synthetic-report', 'synthetic/patient-a/report.pdf', 'ready'),
    ('40000000-0000-4000-8000-000000000002', '20000000-0000-4000-8000-000000000002', 'synthetic-report', 'synthetic/patient-b/report.pdf', 'ready');

insert into public.document_chunk (
    chunk_id, doc_id, patient_id, page_number, text_content, extraction_version
)
values
    ('50000000-0000-4000-8000-000000000001', '40000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000001', 1, 'Synthetic result for patient A.', 'test-v1'),
    ('50000000-0000-4000-8000-000000000002', '40000000-0000-4000-8000-000000000002', '20000000-0000-4000-8000-000000000002', 1, 'Synthetic result for patient B.', 'test-v1');

insert into public.audit_event (
    event_id, actor_id, actor_role, purpose, action, resource_type,
    resource_id, decision, trace_id
)
values
    ('60000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001', 'patient', 'records', 'read', 'document', '40000000-0000-4000-8000-000000000001', 'allow', 'test-owner-trace'),
    ('60000000-0000-4000-8000-000000000002', '10000000-0000-4000-8000-000000000002', 'patient', 'records', 'read', 'document', '40000000-0000-4000-8000-000000000002', 'allow', 'test-other-trace');

-- A patient can see their own record but not another patient's record.
set local role authenticated;
select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000001', true);
select set_config('request.jwt.claims', '{"sub":"10000000-0000-4000-8000-000000000001","role":"authenticated","purpose":"records","app_metadata":{}}', true);

do $$
begin
    if (select count(*) from public.patient) <> 1 then
        raise exception 'patient RLS did not restrict rows to the authenticated patient';
    end if;
    if (select count(*) from public.document) <> 1 then
        raise exception 'document RLS exposed another patient or hid the owner's document';
    end if;
    if (select count(*) from public.document_chunk) <> 1 then
        raise exception 'document chunk RLS exposed another patient or hid the owner chunk';
    end if;
    if (select count(*) from public.audit_event) <> 1 then
        raise exception 'audit RLS exposed events belonging to another actor';
    end if;
end;
$$;

-- A matching, active grant permits only its grantee to read the scoped resource.
select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000003', true);
select set_config('request.jwt.claims', '{"sub":"10000000-0000-4000-8000-000000000003","role":"authenticated","purpose":"records","app_metadata":{}}', true);
do $$
begin
    if (select count(*) from public.document) <> 1 then
        raise exception 'active consent did not grant scoped document access';
    end if;
    if (select count(*) from public.patient) <> 0 then
        raise exception 'records-only consent incorrectly granted patient profile access';
    end if;
end;
$$;

-- Expired, revoked, wrong-purpose, and wrong-scope grants all deny access.
select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000004', true);
select set_config('request.jwt.claims', '{"sub":"10000000-0000-4000-8000-000000000004","role":"authenticated","purpose":"records","app_metadata":{}}', true);
do $$ begin
    if (select count(*) from public.document) <> 0 then raise exception 'expired consent allowed document access'; end if;
end $$;

select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000005', true);
select set_config('request.jwt.claims', '{"sub":"10000000-0000-4000-8000-000000000005","role":"authenticated","purpose":"records","app_metadata":{}}', true);
do $$ begin
    if (select count(*) from public.document) <> 0 then raise exception 'revoked consent allowed document access'; end if;
end $$;

select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000006', true);
select set_config('request.jwt.claims', '{"sub":"10000000-0000-4000-8000-000000000006","role":"authenticated","purpose":"booking","app_metadata":{}}', true);
do $$ begin
    if (select count(*) from public.document) <> 0 then raise exception 'wrong-purpose consent allowed document access'; end if;
end $$;

select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000007', true);
select set_config('request.jwt.claims', '{"sub":"10000000-0000-4000-8000-000000000007","role":"authenticated","purpose":"records","app_metadata":{}}', true);
do $$ begin
    if (select count(*) from public.document) <> 0 then raise exception 'wrong-scope consent allowed document access'; end if;
end $$;

-- Compliance can inspect audit events, but not patient clinical records absent consent.
select set_config('request.jwt.claim.sub', '10000000-0000-4000-8000-000000000008', true);
select set_config('request.jwt.claims', '{"sub":"10000000-0000-4000-8000-000000000008","role":"authenticated","purpose":"compliance","app_metadata":{"role":"compliance"}}', true);
do $$
begin
    if (select count(*) from public.audit_event) <> 2 then
        raise exception 'compliance role could not read audit events';
    end if;
    if (select count(*) from public.document) <> 0 then
        raise exception 'compliance role unexpectedly received patient document access';
    end if;
end;
$$;

reset role;

-- The prior migration's signed-note and audit append-only guards remain effective.
insert into public.encounter (encounter_id, patient_id)
values ('70000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000001');
insert into public.clinical_note_version (
    note_id, encounter_id, version, draft_json, signed_by, signed_at, content_hash
)
values (
    '80000000-0000-4000-8000-000000000001',
    '70000000-0000-4000-8000-000000000001',
    1,
    '{"subjective":"synthetic note"}',
    '10000000-0000-4000-8000-000000000001',
    now(),
    'synthetic-content-hash'
);

do $$
begin
    begin
        update public.clinical_note_version
        set draft_json = '{"subjective":"tampered"}'
        where note_id = '80000000-0000-4000-8000-000000000001' and version = 1;
        raise exception 'signed note mutation unexpectedly succeeded';
    exception when sqlstate '55000' then
        null;
    end;

    begin
        update public.audit_event
        set decision = 'tampered'
        where event_id = '60000000-0000-4000-8000-000000000001';
        raise exception 'audit event mutation unexpectedly succeeded';
    exception when sqlstate '55000' then
        null;
    end;
end;
$$;

rollback;
