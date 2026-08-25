-- MeetMind AI shared workspace schema
-- Run this whole file in Supabase: SQL Editor -> New query -> Run

create extension if not exists "pgcrypto";

create table if not exists public.workspaces (
  id uuid primary key default gen_random_uuid(),
  code text unique not null,
  name text not null,
  password_hash text not null,
  created_at timestamptz not null default now()
);

create table if not exists public.members (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  name text not null,
  contact text not null,
  created_at timestamptz not null default now(),
  unique(workspace_id, contact)
);

create table if not exists public.meetings (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  title text not null,
  meeting_date text not null,
  participants text,
  summary text,
  transcript text,
  task_count integer not null default 0,
  completion_pct double precision not null default 0,
  created_by text,
  created_at timestamptz not null default now()
);

alter table public.workspaces disable row level security;
alter table public.members disable row level security;
alter table public.meetings disable row level security;

grant usage on schema public to anon, authenticated;
grant select, insert, update, delete on public.workspaces to anon, authenticated;
grant select, insert, update, delete on public.members to anon, authenticated;
grant select, insert, update, delete on public.meetings to anon, authenticated;


-- Shared task status persistence
create table if not exists public.action_items (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  meeting_id uuid not null references public.meetings(id) on delete cascade,
  local_id integer not null,
  task text not null,
  owner text,
  deadline text,
  priority text,
  status text not null default 'Pending',
  dependency text,
  context text,
  confidence double precision,
  updated_by text,
  updated_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  unique(meeting_id, local_id)
);

alter table public.action_items disable row level security;
grant select, insert, update, delete on public.action_items to anon, authenticated;

-- Optional quick verification:
-- select table_name from information_schema.tables
-- where table_schema='public'
-- and table_name in ('workspaces','members','meetings');
