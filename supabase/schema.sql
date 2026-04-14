create extension if not exists pgcrypto;

create table if not exists public.app_users (
    id uuid primary key default gen_random_uuid(),
    username text not null unique,
    password_hash text not null,
    role text not null check (role in ('admin', 'member')),
    is_active boolean not null default true,
    created_by uuid,
    created_at timestamptz not null default now()
);

create table if not exists public.app_config (
    id text primary key,
    registration_enabled boolean not null default true,
    registration_password_hash text,
    enabled_models text[] not null default array['openai/gpt-5.4', 'anthropic/claude-sonnet-4.6', 'openrouter/elephant-alpha'],
    updated_at timestamptz not null default now()
);

create table if not exists public.chat_logs (
    id bigint generated always as identity primary key,
    user_id uuid,
    username text not null,
    user_role text not null,
    session_id text,
    request_payload jsonb not null default '{}'::jsonb,
    response_payload jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create table if not exists public.admin_logs (
    id bigint generated always as identity primary key,
    admin_user_id uuid,
    admin_username text not null,
    action text not null,
    target_type text not null,
    target_id text,
    details jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_app_users_username on public.app_users (username);
create index if not exists idx_chat_logs_created_at on public.chat_logs (created_at desc);
create index if not exists idx_admin_logs_created_at on public.admin_logs (created_at desc);

insert into public.app_config (id, registration_enabled, registration_password_hash, enabled_models)
values (
    'main',
    true,
    null,
    array['openai/gpt-5.4', 'anthropic/claude-sonnet-4.6', 'openrouter/elephant-alpha']
)
on conflict (id) do update
set
    registration_enabled = excluded.registration_enabled,
    enabled_models = excluded.enabled_models,
    updated_at = now();

insert into public.app_users (username, password_hash, role, is_active)
values (
    'vocflight',
    'scrypt:32768:8:1$Iv7jISOMO6uRNEsI$e20085e1b429a872aee8f93193657dcda69b8830ad6cbf603eab1d84d88602a72c08400aba5461d71d6aa73c251033d98c81678ef486e5f4c4ffb5ea8f224b12',
    'admin',
    true
)
on conflict (username) do update
set
    password_hash = excluded.password_hash,
    role = 'admin',
    is_active = true;
