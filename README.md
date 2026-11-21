# SpaceXHackathon

Fin Crime Hackathon datasets and tooling.

## Data

Hackathon source files live under [`hackathon/`](hackathon/) (`institutions/{bank,payments_fx,emoney,investment}/`).

## Supabase load

Schema migration + idempotent Python loader:

- [`scripts/supabase/README.md`](scripts/supabase/README.md)
- Migration: `scripts/supabase/migrations/001_hackathon_schema.sql`
- Loader: `scripts/supabase/load_hackathon.py`

Requires `SUPABASE_DB_URL` (or `DATABASE_URL` / `SUPABASE_URL`+`SUPABASE_DB_PASSWORD`). See the loader README for details.
