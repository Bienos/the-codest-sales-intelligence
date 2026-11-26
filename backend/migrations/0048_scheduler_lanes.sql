-- The scheduler's automatic work, one row per lane (data pipeline review, 26 Nov 2026).
--
-- The 5-minute scheduler ran every housekeeping step on every tick, including steps that only matter once an hour or after a rule
-- change. Each lane now records when it last ran and what it did, how often it should run, and whether it is switched on, so
-- Administration can show "last run / next run / what it found" and a person can pause a lane or ask for it to run at the next tick.
-- A lane with no row runs on its built-in cadence (ri.scheduler.LANES), so an empty table behaves exactly as before.
--
-- New table only; reversible by dropping it.
CREATE TABLE IF NOT EXISTS scheduler_lanes (
  lane         text PRIMARY KEY,
  enabled      boolean NOT NULL DEFAULT true,
  every_s      integer CHECK (every_s IS NULL OR every_s BETWEEN 60 AND 604800),   -- NULL: the lane's built-in cadence
  last_run_at  timestamptz,
  last_result  jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(last_result) = 'object'),
  last_error   text,
  runs         integer NOT NULL DEFAULT 0,
  run_requested_by text,                                                           -- a person asked for it to run at the next tick
  updated_by   text,
  updated_at   timestamptz NOT NULL DEFAULT now()
);

-- Same baseline as every other table: no access through the public REST roles.
DO $$
BEGIN
  ALTER TABLE public.scheduler_lanes ENABLE ROW LEVEL SECURITY;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON public.scheduler_lanes FROM anon; END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON public.scheduler_lanes FROM authenticated; END IF;
END $$;
