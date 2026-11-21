# Hackathon → Supabase Postgres loader

Loads the Fin Crime Hackathon datasets under `hackathon/institutions/{bank,payments_fx,emoney,investment}/` into a single Postgres schema `hackathon` on Supabase.

## Schema summary

| Table | PK | Notes |
|---|---|---|
| `hackathon.customers` | `(institution, customer_id)` | `declared.*` flattened; `account_ids` + expected countries/channels as `text[]` |
| `hackathon.cases` | `(institution, case_id)` | `review_window` → `review_window_from` / `review_window_to`; `alert_ids text[]` |
| `hackathon.alerts` | `(institution, alert_id)` | `measured` kept as **jsonb**; `transaction_ids text[]` |
| `hackathon.transactions` | `(institution, transaction_id)` | flat monetary + counterparty fields |
| `hackathon.evidence` | `(institution, doc_id)` | `file_path` + `body_md` (markdown content) |
| `hackathon.previous_reviews` | `(institution, review_id)` | |
| `hackathon.internal_list` | `(institution, entry_id)` | `identifiers` → `identifier_internal_case`, `identifier_swift` |
| `hackathon.policies` | `institution` | `POLICY.md` body |
| `hackathon.case_alerts` | bridge | case ↔ alert |
| `hackathon.alert_transactions` | bridge | alert ↔ transaction |
| `hackathon.customer_accounts` | bridge | customer ↔ account |

Migration: [`migrations/001_hackathon_schema.sql`](migrations/001_hackathon_schema.sql)

## Credentials (never commit)

Set **one** of:

| Variable | Purpose |
|---|---|
| `SUPABASE_DB_URL` | **Preferred.** Postgres URI from Supabase → Project Settings → Database → Connection string (URI). Use the direct or session pooler URI with the DB password. |
| `DATABASE_URL` | Alias for the same Postgres URI |
| `SUPABASE_URL` + `SUPABASE_DB_PASSWORD` | Convenience: builds `postgresql://postgres:…@db.<ref>.supabase.co:5432/postgres?sslmode=require` |

Optional (not required for this loader):

| Variable | Purpose |
|---|---|
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase API service role — unused by the direct Postgres loader |

Copy [`.env.example`](.env.example) to a local `.env` (gitignored) if you load credentials that way.

## Install

```bash
pip install -r scripts/supabase/requirements.txt
```

## Run

From the repo root (auto-detects `hackathon/` or `/workspace/hackathon`):

```bash
export SUPABASE_DB_URL='postgresql://postgres.<ref>:<password>@aws-0-….pooler.supabase.com:5432/postgres'
python scripts/supabase/load_hackathon.py
```

Options:

```bash
python scripts/supabase/load_hackathon.py --data-dir ./hackathon
python scripts/supabase/load_hackathon.py --institutions bank,emoney
python scripts/supabase/load_hackathon.py --skip-migrate
python scripts/supabase/load_hackathon.py --verify-only
```

The loader:

1. Applies the SQL migration (idempotent `CREATE … IF NOT EXISTS`)
2. Upserts all JSONL rows in batches (`ON CONFLICT DO UPDATE`)
3. Reads each evidence `*.md` into `body_md`
4. Prints verification counts

Re-runs are safe (upserts on primary keys).

## Verify

```bash
psql "$SUPABASE_DB_URL" -f scripts/supabase/verify.sql
# or
python scripts/supabase/load_hackathon.py --verify-only
```

Expected JSONL totals across four institutions ≈ **242k** entity rows (+ bridge rows + 4 policies).
