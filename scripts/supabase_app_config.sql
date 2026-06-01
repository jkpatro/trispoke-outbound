-- Paste into Supabase SQL Editor and click Run.
-- Plaintext operational settings (NOT secrets) edited from the in-app
-- Settings page: daily cap, ramp schedule, pace, bounce guardrails, Apollo
-- throughput. Replaces the env-var-only configuration so end users never have
-- to touch .env. Secrets keep living (Fernet-encrypted) in app_secrets.
-- Idempotent: safe to re-run.

CREATE TABLE IF NOT EXISTS public.app_config (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.app_config ENABLE ROW LEVEL SECURITY;

-- Admins only (matches app_secrets). The app reads/writes via the direct
-- Postgres connection (DATABASE_URL), which bypasses RLS; these policies gate
-- access from PostgREST / user-JWT connections.
DROP POLICY IF EXISTS "admins_read_config"   ON public.app_config;
CREATE POLICY "admins_read_config"   ON public.app_config
  FOR SELECT USING (public.current_user_role() = 'admin');

DROP POLICY IF EXISTS "admins_write_config"  ON public.app_config;
CREATE POLICY "admins_write_config"  ON public.app_config
  FOR INSERT WITH CHECK (public.current_user_role() = 'admin');

DROP POLICY IF EXISTS "admins_update_config" ON public.app_config;
CREATE POLICY "admins_update_config" ON public.app_config
  FOR UPDATE USING (public.current_user_role() = 'admin');

DROP POLICY IF EXISTS "admins_delete_config" ON public.app_config;
CREATE POLICY "admins_delete_config" ON public.app_config
  FOR DELETE USING (public.current_user_role() = 'admin');
