-- Paste into Supabase SQL Editor and click Run.
-- Adds:
--  (1) profiles.full_name — editable display name
--  (2) app_secrets        — all non-Supabase credentials live here, Fernet-encrypted
--                           at the application layer. The DB never sees plaintext.
--                           Only admins can read or write via RLS.
-- Idempotent: safe to re-run.

ALTER TABLE public.profiles
  ADD COLUMN IF NOT EXISTS full_name TEXT;

CREATE TABLE IF NOT EXISTS public.app_secrets (
  key_name TEXT PRIMARY KEY,
  encrypted_value TEXT NOT NULL,
  last_set_by UUID REFERENCES auth.users(id) ON DELETE SET NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.app_secrets ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "admins_read_secrets" ON public.app_secrets;
CREATE POLICY "admins_read_secrets" ON public.app_secrets
  FOR SELECT USING (public.current_user_role() = 'admin');

DROP POLICY IF EXISTS "admins_write_secrets" ON public.app_secrets;
CREATE POLICY "admins_write_secrets" ON public.app_secrets
  FOR INSERT WITH CHECK (public.current_user_role() = 'admin');

DROP POLICY IF EXISTS "admins_update_secrets" ON public.app_secrets;
CREATE POLICY "admins_update_secrets" ON public.app_secrets
  FOR UPDATE USING (public.current_user_role() = 'admin');

DROP POLICY IF EXISTS "admins_delete_secrets" ON public.app_secrets;
CREATE POLICY "admins_delete_secrets" ON public.app_secrets
  FOR DELETE USING (public.current_user_role() = 'admin');

-- App-layer access via the service-role connection (DATABASE_URL) bypasses RLS,
-- so background workers can still call get_secret() without a JWT. The RLS
-- above gates access from Supabase client connections (e.g. anyone hitting
-- the project's REST endpoint with a user JWT — they'd need admin role).

CREATE INDEX IF NOT EXISTS idx_app_secrets_updated_at ON public.app_secrets(updated_at DESC);
