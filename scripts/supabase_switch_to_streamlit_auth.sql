-- Paste into Supabase SQL Editor and click Run.
-- We are switching from Supabase OAuth to Streamlit's native `st.login()` OIDC
-- for sign-in. Profiles are no longer keyed off auth.users.id — they live
-- standalone, keyed by email. This drops the FK + auth.users trigger.
-- Idempotent.

ALTER TABLE public.profiles
  DROP CONSTRAINT IF EXISTS profiles_id_fkey;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
DROP FUNCTION IF EXISTS public.handle_new_user();

-- last_set_by on app_secrets also referenced auth.users; switch it to reference
-- profiles instead, so audit trails survive the auth rip-out.
ALTER TABLE public.app_secrets
  DROP CONSTRAINT IF EXISTS app_secrets_last_set_by_fkey;

ALTER TABLE public.app_secrets
  ADD CONSTRAINT app_secrets_last_set_by_fkey
  FOREIGN KEY (last_set_by) REFERENCES public.profiles(id) ON DELETE SET NULL;

-- invited_by on user_invitations referenced auth.users; switch to profiles too.
ALTER TABLE public.user_invitations
  DROP CONSTRAINT IF EXISTS user_invitations_invited_by_fkey;

ALTER TABLE public.user_invitations
  ADD CONSTRAINT user_invitations_invited_by_fkey
  FOREIGN KEY (invited_by) REFERENCES public.profiles(id) ON DELETE SET NULL;

-- Faster profile-by-email lookups, since email is now the natural key in code.
CREATE UNIQUE INDEX IF NOT EXISTS uq_profiles_email_ci
  ON public.profiles (LOWER(email));
