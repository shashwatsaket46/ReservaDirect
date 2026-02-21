-- ─────────────────────────────────────────────────────────────────────────────
-- ReservaDirect — Supabase Schema
-- Run this in: Supabase Dashboard → SQL Editor → New Query → Run
-- ─────────────────────────────────────────────────────────────────────────────

-- booking_sessions: persists agent loop state between WhatsApp turns
CREATE TABLE IF NOT EXISTS public.booking_sessions (
    session_id          TEXT PRIMARY KEY,
    user_phone          TEXT NOT NULL,
    messages            JSONB NOT NULL DEFAULT '[]'::jsonb,
    booking_status      TEXT NOT NULL DEFAULT 'searching',
    pending_approval    JSONB,
    result_index        INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS booking_sessions_user_phone_idx
    ON public.booking_sessions (user_phone);

CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS booking_sessions_updated_at ON public.booking_sessions;
CREATE TRIGGER booking_sessions_updated_at
    BEFORE UPDATE ON public.booking_sessions
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- Add Stripe columns to existing profiles table (safe to re-run)
ALTER TABLE public.profiles
    ADD COLUMN IF NOT EXISTS stripe_customer_id       TEXT,
    ADD COLUMN IF NOT EXISTS stripe_payment_method_id TEXT;

-- RLS: backend service role bypasses automatically; block direct client access
ALTER TABLE public.booking_sessions ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'booking_sessions'
        AND policyname = 'No direct client access to booking_sessions'
    ) THEN
        EXECUTE $policy$
            CREATE POLICY "No direct client access to booking_sessions"
                ON public.booking_sessions FOR ALL
                TO anon, authenticated
                USING (false)
        $policy$;
    END IF;
END $$;