-- Paste into Supabase SQL Editor and click Run.
-- Switches sign-in from Google OAuth (st.login) to Supabase Auth (GoTrue)
-- email + password. Profiles stay keyed by email (the natural key in code);
-- we add an explicit link to the GoTrue user id so admin password resets can
-- target the right auth.users row.
--
-- The OAuth-era DB logic (handle_new_user trigger + profiles.id FK to
-- auth.users) was already removed in supabase_switch_to_streamlit_auth.sql,
-- so there is nothing OAuth-specific left to drop here.
--
-- Idempotent: safe to re-run.

-- (1) Link each profile to its GoTrue auth.users.id. We deliberately do NOT add
--     a hard FK: the existing bootstrap profile predates this column and a strict
--     constraint would reject rows whose GoTrue user hasn't been reconciled yet.
ALTER TABLE public.profiles
  ADD COLUMN IF NOT EXISTS auth_uid UUID;

-- One profile per GoTrue user (NULLs allowed for not-yet-reconciled rows).
CREATE UNIQUE INDEX IF NOT EXISTS uq_profiles_auth_uid
  ON public.profiles (auth_uid)
  WHERE auth_uid IS NOT NULL;

-- (2) Backfill: where a profile's id already equals an existing auth.users.id
--     (true for the bootstrap admin, whose profile id == auth uid), record it.
UPDATE public.profiles p
SET auth_uid = p.id
FROM auth.users u
WHERE u.id = p.id
  AND p.auth_uid IS NULL;

-- (3) Make sure the bootstrap owner stays admin + active regardless of history.
UPDATE public.profiles
SET role = 'admin'::public.user_role,
    is_active = TRUE
WHERE LOWER(email) = 'jkpatro@gmail.com';
