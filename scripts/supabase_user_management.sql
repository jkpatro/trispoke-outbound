-- Paste into Supabase SQL Editor and click Run.
-- Adds proper user management:
--  (1) profiles.is_active            — admin can deactivate without deleting
--  (2) user_invitations              — pre-approved emails admins have invited;
--                                      consumed by the trigger on first OAuth sign-in
--  (3) handle_new_user (updated)     — respects invitations + auto-admin jkpatro
--                                      + new sign-ups land inactive by default
--  (4) RLS on user_invitations       — admins only
--  (5) Backfill jkpatro              — sets is_active=true + ensures self-invite row
-- Idempotent: safe to re-run.

ALTER TABLE public.profiles
  ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

CREATE TABLE IF NOT EXISTS public.user_invitations (
  email      TEXT PRIMARY KEY,
  role       public.user_role NOT NULL DEFAULT 'user',
  invited_by UUID REFERENCES auth.users(id) ON DELETE SET NULL,
  invited_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.user_invitations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "admins_read_invitations"   ON public.user_invitations;
CREATE POLICY "admins_read_invitations"   ON public.user_invitations
  FOR SELECT USING (public.current_user_role() = 'admin');

DROP POLICY IF EXISTS "admins_write_invitations"  ON public.user_invitations;
CREATE POLICY "admins_write_invitations"  ON public.user_invitations
  FOR INSERT WITH CHECK (public.current_user_role() = 'admin');

DROP POLICY IF EXISTS "admins_update_invitations" ON public.user_invitations;
CREATE POLICY "admins_update_invitations" ON public.user_invitations
  FOR UPDATE USING (public.current_user_role() = 'admin');

DROP POLICY IF EXISTS "admins_delete_invitations" ON public.user_invitations;
CREATE POLICY "admins_delete_invitations" ON public.user_invitations
  FOR DELETE USING (public.current_user_role() = 'admin');

-- Trigger: on Google sign-up, look at the invitations table and consume the row
-- if present. Without an invitation, the user lands inactive — they can sign in
-- but the app gates them with "pending admin approval" until an admin flips
-- is_active.
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_role   public.user_role;
  v_active BOOLEAN;
BEGIN
  -- Bootstrap: jkpatro is the workspace owner and always admin/active.
  IF LOWER(NEW.email) = 'jkpatro@gmail.com' THEN
    INSERT INTO public.user_invitations (email, role)
    VALUES (NEW.email, 'admin'::public.user_role)
    ON CONFLICT (email) DO UPDATE SET role = EXCLUDED.role;
  END IF;

  -- Consume the invitation if one exists.
  SELECT role INTO v_role
  FROM public.user_invitations
  WHERE LOWER(email) = LOWER(NEW.email);

  IF v_role IS NOT NULL THEN
    v_active := TRUE;
    DELETE FROM public.user_invitations WHERE LOWER(email) = LOWER(NEW.email);
  ELSE
    -- Unknown email — land them inactive. Admin can activate from the UI.
    v_role   := 'user'::public.user_role;
    v_active := FALSE;
  END IF;

  -- Defence-in-depth: on a trigger re-fire for the same auth.users row, never
  -- overwrite role or is_active — those may have been changed by an admin
  -- through the UI since the row was first created. Email is the only field
  -- safe to refresh (auth.users.email is the source of truth).
  INSERT INTO public.profiles (id, email, role, is_active)
  VALUES (NEW.id, NEW.email, v_role, v_active)
  ON CONFLICT (id) DO UPDATE
    SET email = EXCLUDED.email;
  RETURN NEW;
END;
$$;

-- Defence-in-depth: stop an admin from inviting an email that already has a
-- profile. The UI checks for this, but a direct SQL insert (or a future
-- code path that forgets the check) would otherwise create a ghost invitation
-- that never gets consumed because the user is already signed up.
CREATE OR REPLACE FUNCTION public.user_invitations_block_existing_profiles()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM public.profiles WHERE LOWER(email) = LOWER(NEW.email)) THEN
    RAISE EXCEPTION 'profile_exists' USING ERRCODE = 'unique_violation';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS user_invitations_block_existing_profiles_trigger ON public.user_invitations;
CREATE TRIGGER user_invitations_block_existing_profiles_trigger
  BEFORE INSERT ON public.user_invitations
  FOR EACH ROW EXECUTE FUNCTION public.user_invitations_block_existing_profiles();

-- Backfill: ensure jkpatro is active. Other existing users keep whatever they had.
UPDATE public.profiles
SET is_active = TRUE
WHERE LOWER(email) = 'jkpatro@gmail.com';

CREATE INDEX IF NOT EXISTS idx_profiles_is_active ON public.profiles(is_active);
CREATE INDEX IF NOT EXISTS idx_user_invitations_invited_at ON public.user_invitations(invited_at DESC);
