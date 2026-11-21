#!/usr/bin/env python3
"""Load Fin Crime Hackathon JSONL datasets into Supabase Postgres.

Reads only the hackathon/institutions tree. Credentials via env:
  SUPABASE_DB_URL              preferred (Postgres connection string)
  or DATABASE_URL              alias
  or SUPABASE_URL + SUPABASE_DB_PASSWORD / SUPABASE_SERVICE_ROLE_KEY
    (DB URL must still be set for direct Postgres; service role alone is not enough
     for COPY-style bulk load — use the Supabase Dashboard → Database → URI)

Usage:
  python load_hackathon.py [--data-dir PATH] [--skip-migrate] [--institutions a,b]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

try:
    import psycopg
    from psycopg import sql
    from psycopg.types.json import Jsonb
except ImportError:
    print(
        "Missing dependency: psycopg. Install with:\n"
        "  pip install -r scripts/supabase/requirements.txt",
        file=sys.stderr,
    )
    sys.exit(1)

INSTITUTIONS = ("bank", "payments_fx", "emoney", "investment")
BATCH_SIZE = 2000

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


def resolve_db_url() -> str:
    """Resolve Postgres URL from env. Never print the value."""
    for key in ("SUPABASE_DB_URL", "DATABASE_URL"):
        val = os.environ.get(key, "").strip()
        if val:
            return val

    # Supabase sometimes exposes host pieces separately — still require a URL.
    url = os.environ.get("SUPABASE_URL", "").strip()
    password = os.environ.get("SUPABASE_DB_PASSWORD", "").strip()
    if url and password:
        # Build from project ref if SUPABASE_URL looks like https://<ref>.supabase.co
        # Prefer explicit SUPABASE_DB_URL; this is a convenience fallback only.
        host = url.removeprefix("https://").removeprefix("http://").rstrip("/")
        if host.endswith(".supabase.co"):
            ref = host.split(".")[0]
            return (
                f"postgresql://postgres:{password}"
                f"@db.{ref}.supabase.co:5432/postgres"
                f"?sslmode=require"
            )

    missing = [
        "SUPABASE_DB_URL (preferred Postgres URI from Supabase → Database → Connection string)",
        "or DATABASE_URL",
        "or SUPABASE_URL + SUPABASE_DB_PASSWORD",
    ]
    print("No database credentials found in the environment.", file=sys.stderr)
    print("Set one of:", file=sys.stderr)
    for m in missing:
        print(f"  - {m}", file=sys.stderr)
    print(
        "Optional (not used for direct Postgres load): SUPABASE_SERVICE_ROLE_KEY",
        file=sys.stderr,
    )
    sys.exit(2)


def connect():
    return psycopg.connect(resolve_db_url(), autocommit=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    s = str(value)
    if "T" in s:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    return date.fromisoformat(s[:10])


def parse_ts(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def parse_num(value: Any):
    if value is None or value == "":
        return None
    return value


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {path}:{line_no}") from exc


def batched(rows: Iterable[Sequence[Any]], size: int) -> Iterator[list[Sequence[Any]]]:
    batch: list[Sequence[Any]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def upsert_batches(
    cur,
    table: str,
    columns: Sequence[str],
    rows: Iterable[Sequence[Any]],
    conflict: Sequence[str],
    batch_size: int = BATCH_SIZE,
) -> int:
    col_list = sql.SQL(", ").join(map(sql.Identifier, columns))
    placeholders = sql.SQL(", ").join(sql.Placeholder() * len(columns))
    conflict_cols = sql.SQL(", ").join(map(sql.Identifier, conflict))
    update_cols = [c for c in columns if c not in conflict]
    if update_cols:
        set_clause = sql.SQL(", ").join(
            sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(c), sql.Identifier(c))
            for c in update_cols
        )
        stmt = sql.SQL(
            "INSERT INTO {} ({}) VALUES ({}) ON CONFLICT ({}) DO UPDATE SET {}"
        ).format(
            sql.SQL(table),
            col_list,
            placeholders,
            conflict_cols,
            set_clause,
        )
    else:
        stmt = sql.SQL(
            "INSERT INTO {} ({}) VALUES ({}) ON CONFLICT ({}) DO NOTHING"
        ).format(sql.SQL(table), col_list, placeholders, conflict_cols)

    total = 0
    for batch in batched(rows, batch_size):
        cur.executemany(stmt, batch)
        total += len(batch)
    return total


# ---------------------------------------------------------------------------
# Row mappers
# ---------------------------------------------------------------------------


def map_customer(institution: str, row: dict) -> tuple:
    declared = row.get("declared") or {}
    return (
        institution,
        row["customer_id"],
        row["kind"],
        row["name"],
        parse_date(row.get("date_of_birth")),
        row.get("occupation"),
        row.get("employer"),
        row.get("company_number"),
        parse_date(row.get("incorporated_on")),
        row.get("business_activity"),
        row.get("address"),
        parse_date(row.get("onboarded_on")),
        row.get("risk_rating"),
        declared.get("source_of_funds"),
        declared.get("account_purpose"),
        list(declared.get("expected_countries") or []),
        list(declared.get("expected_channels") or []),
        parse_num(declared.get("monthly_income_gbp")),
        parse_num(declared.get("annual_turnover_gbp")),
        list(row.get("account_ids") or []),
    )


CUSTOMER_COLS = [
    "institution",
    "customer_id",
    "kind",
    "name",
    "date_of_birth",
    "occupation",
    "employer",
    "company_number",
    "incorporated_on",
    "business_activity",
    "address",
    "onboarded_on",
    "risk_rating",
    "declared_source_of_funds",
    "declared_account_purpose",
    "declared_expected_countries",
    "declared_expected_channels",
    "declared_monthly_income_gbp",
    "declared_annual_turnover_gbp",
    "account_ids",
]


def map_case(institution: str, row: dict) -> tuple:
    window = row.get("review_window") or {}
    return (
        institution,
        row["case_id"],
        row["customer_id"],
        parse_ts(row["opened_at"]),
        parse_date(window.get("from")),
        parse_date(window.get("to")),
        list(row.get("alert_ids") or []),
        row.get("held_payment_id"),
    )


CASE_COLS = [
    "institution",
    "case_id",
    "customer_id",
    "opened_at",
    "review_window_from",
    "review_window_to",
    "alert_ids",
    "held_payment_id",
]


def map_alert(institution: str, row: dict) -> tuple:
    return (
        institution,
        row["alert_id"],
        row["case_id"],
        row["customer_id"],
        row["rule_name"],
        row["rule_id"],
        parse_ts(row["created_at"]),
        row["message"],
        Jsonb(row.get("measured") or {}),
        list(row.get("transaction_ids") or []),
    )


ALERT_COLS = [
    "institution",
    "alert_id",
    "case_id",
    "customer_id",
    "rule_name",
    "rule_id",
    "created_at",
    "message",
    "measured",
    "transaction_ids",
]


def map_transaction(institution: str, row: dict) -> tuple:
    return (
        institution,
        row["transaction_id"],
        row["customer_id"],
        row["account_id"],
        parse_ts(row["booked_at"]),
        row["direction"],
        row["channel"],
        parse_num(row["amount_gbp"]),
        parse_num(row["amount"]),
        row["currency"],
        row.get("counterparty_name"),
        row.get("counterparty_account"),
        row.get("counterparty_country"),
        row.get("reference"),
        row.get("location"),
    )


TX_COLS = [
    "institution",
    "transaction_id",
    "customer_id",
    "account_id",
    "booked_at",
    "direction",
    "channel",
    "amount_gbp",
    "amount",
    "currency",
    "counterparty_name",
    "counterparty_account",
    "counterparty_country",
    "reference",
    "location",
]


def map_evidence(institution: str, row: dict, body: str | None) -> tuple:
    return (
        institution,
        row["doc_id"],
        row["customer_id"],
        row["type"],
        row["title"],
        parse_date(row.get("dated")),
        row["file"],
        body,
    )


EVIDENCE_COLS = [
    "institution",
    "doc_id",
    "customer_id",
    "type",
    "title",
    "dated",
    "file_path",
    "body_md",
]


def map_review(institution: str, row: dict) -> tuple:
    return (
        institution,
        row["review_id"],
        row["customer_id"],
        parse_date(row.get("reviewed_on")),
        row.get("rule_id"),
        row.get("verdict"),
        row.get("note"),
    )


REVIEW_COLS = [
    "institution",
    "review_id",
    "customer_id",
    "reviewed_on",
    "rule_id",
    "verdict",
    "note",
]


def map_internal(institution: str, row: dict) -> tuple:
    ids = row.get("identifiers") or {}
    return (
        institution,
        row["entry_id"],
        row["kind"],
        row["name"],
        parse_date(row.get("date_of_birth")),
        row.get("address"),
        row.get("reason"),
        parse_date(row.get("added_on")),
        ids.get("internal_case"),
        ids.get("swift"),
    )


INTERNAL_COLS = [
    "institution",
    "entry_id",
    "kind",
    "name",
    "date_of_birth",
    "address",
    "reason",
    "added_on",
    "identifier_internal_case",
    "identifier_swift",
]


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def apply_migration(cur, migration_path: Path) -> None:
    sql_text = migration_path.read_text(encoding="utf-8")
    cur.execute(sql_text)


def load_institution(cur, institution: str, inst_dir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}

    # customers
    cust_rows = [map_customer(institution, r) for r in iter_jsonl(inst_dir / "customers.jsonl")]
    counts["customers"] = upsert_batches(
        cur, "hackathon.customers", CUSTOMER_COLS, cust_rows, ("institution", "customer_id")
    )
    acct_rows = [
        (institution, r["customer_id"], aid)
        for r in iter_jsonl(inst_dir / "customers.jsonl")
        for aid in (r.get("account_ids") or [])
    ]
    counts["customer_accounts"] = upsert_batches(
        cur,
        "hackathon.customer_accounts",
        ("institution", "customer_id", "account_id"),
        acct_rows,
        ("institution", "customer_id", "account_id"),
    )

    # cases
    case_rows = [map_case(institution, r) for r in iter_jsonl(inst_dir / "cases.jsonl")]
    counts["cases"] = upsert_batches(
        cur, "hackathon.cases", CASE_COLS, case_rows, ("institution", "case_id")
    )
    case_alert_rows = [
        (institution, r["case_id"], aid)
        for r in iter_jsonl(inst_dir / "cases.jsonl")
        for aid in (r.get("alert_ids") or [])
    ]
    counts["case_alerts"] = upsert_batches(
        cur,
        "hackathon.case_alerts",
        ("institution", "case_id", "alert_id"),
        case_alert_rows,
        ("institution", "case_id", "alert_id"),
    )

    # alerts
    alert_rows = [map_alert(institution, r) for r in iter_jsonl(inst_dir / "alerts.jsonl")]
    counts["alerts"] = upsert_batches(
        cur, "hackathon.alerts", ALERT_COLS, alert_rows, ("institution", "alert_id")
    )
    alert_tx_rows = [
        (institution, r["alert_id"], tid)
        for r in iter_jsonl(inst_dir / "alerts.jsonl")
        for tid in (r.get("transaction_ids") or [])
    ]
    counts["alert_transactions"] = upsert_batches(
        cur,
        "hackathon.alert_transactions",
        ("institution", "alert_id", "transaction_id"),
        alert_tx_rows,
        ("institution", "alert_id", "transaction_id"),
    )

    # transactions (streamed — largest table)
    def tx_iter():
        for r in iter_jsonl(inst_dir / "transactions.jsonl"):
            yield map_transaction(institution, r)

    counts["transactions"] = upsert_batches(
        cur, "hackathon.transactions", TX_COLS, tx_iter(), ("institution", "transaction_id")
    )

    # evidence + markdown bodies
    def evidence_iter():
        for r in iter_jsonl(inst_dir / "evidence.jsonl"):
            body = None
            rel = r.get("file")
            if rel:
                md_path = inst_dir / rel
                if md_path.is_file():
                    body = md_path.read_text(encoding="utf-8")
            yield map_evidence(institution, r, body)

    counts["evidence"] = upsert_batches(
        cur, "hackathon.evidence", EVIDENCE_COLS, evidence_iter(), ("institution", "doc_id")
    )

    # previous reviews
    review_rows = [
        map_review(institution, r) for r in iter_jsonl(inst_dir / "previous_reviews.jsonl")
    ]
    counts["previous_reviews"] = upsert_batches(
        cur,
        "hackathon.previous_reviews",
        REVIEW_COLS,
        review_rows,
        ("institution", "review_id"),
    )

    # internal list
    internal_rows = [
        map_internal(institution, r) for r in iter_jsonl(inst_dir / "internal_list.jsonl")
    ]
    counts["internal_list"] = upsert_batches(
        cur,
        "hackathon.internal_list",
        INTERNAL_COLS,
        internal_rows,
        ("institution", "entry_id"),
    )

    # policy
    policy_path = inst_dir / "POLICY.md"
    if policy_path.is_file():
        body = policy_path.read_text(encoding="utf-8")
        cur.execute(
            """
            INSERT INTO hackathon.policies (institution, file_path, body_md, loaded_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (institution) DO UPDATE
            SET file_path = EXCLUDED.file_path,
                body_md = EXCLUDED.body_md,
                loaded_at = now()
            """,
            (institution, "POLICY.md", body),
        )
        counts["policies"] = 1
    else:
        counts["policies"] = 0

    return counts


VERIFY_SQL = """
SELECT 'customers' AS table_name, institution, count(*)::bigint AS n
FROM hackathon.customers GROUP BY institution
UNION ALL
SELECT 'cases', institution, count(*) FROM hackathon.cases GROUP BY institution
UNION ALL
SELECT 'alerts', institution, count(*) FROM hackathon.alerts GROUP BY institution
UNION ALL
SELECT 'transactions', institution, count(*) FROM hackathon.transactions GROUP BY institution
UNION ALL
SELECT 'evidence', institution, count(*) FROM hackathon.evidence GROUP BY institution
UNION ALL
SELECT 'previous_reviews', institution, count(*) FROM hackathon.previous_reviews GROUP BY institution
UNION ALL
SELECT 'internal_list', institution, count(*) FROM hackathon.internal_list GROUP BY institution
UNION ALL
SELECT 'policies', institution, count(*) FROM hackathon.policies GROUP BY institution
ORDER BY 1, 2;
"""


def run_verify(cur) -> list[tuple]:
    cur.execute(VERIFY_SQL)
    return list(cur.fetchall())


def default_data_dir() -> Path:
    # Prefer /workspace/hackathon (task path) then repo-relative hackathon/
    candidates = [
        Path("/workspace/hackathon"),
        Path(__file__).resolve().parents[2] / "hackathon",
    ]
    for c in candidates:
        if (c / "institutions").is_dir():
            return c
    return candidates[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Hackathon root containing institutions/ (default: auto-detect)",
    )
    parser.add_argument(
        "--skip-migrate",
        action="store_true",
        help="Skip applying 001_hackathon_schema.sql",
    )
    parser.add_argument(
        "--institutions",
        default=",".join(INSTITUTIONS),
        help="Comma-separated institution folder names",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only run verification queries",
    )
    args = parser.parse_args()

    data_dir = args.data_dir or default_data_dir()
    inst_root = data_dir / "institutions"
    institutions = [s.strip() for s in args.institutions.split(",") if s.strip()]

    if not args.verify_only and not inst_root.is_dir():
        print(f"Institutions directory not found: {inst_root}", file=sys.stderr)
        return 1

    migration = Path(__file__).resolve().parent / "migrations" / "001_hackathon_schema.sql"

    print(f"Data dir: {data_dir}")
    print(f"Institutions: {', '.join(institutions)}")
    print("Connecting to Postgres (credentials from env; values not printed)...")

    with connect() as conn:
        with conn.cursor() as cur:
            if args.verify_only:
                rows = run_verify(cur)
                print("\nVerification counts:")
                print(f"{'table':<20} {'institution':<14} {'n':>10}")
                for table, inst, n in rows:
                    print(f"{table:<20} {inst:<14} {n:>10}")
                return 0

            if not args.skip_migrate:
                print(f"Applying migration: {migration.name}")
                apply_migration(cur, migration)
                conn.commit()

            grand: dict[str, int] = {}
            for institution in institutions:
                inst_dir = inst_root / institution
                if not inst_dir.is_dir():
                    print(f"SKIP missing institution dir: {inst_dir}", file=sys.stderr)
                    continue
                print(f"\nLoading {institution} ...")
                counts = load_institution(cur, institution, inst_dir)
                conn.commit()
                for k, v in counts.items():
                    grand[k] = grand.get(k, 0) + v
                    print(f"  {k}: {v}")

            print("\nTotals upserted this run:")
            for k in sorted(grand):
                print(f"  {k}: {grand[k]}")

            rows = run_verify(cur)
            print("\nVerification counts (DB):")
            print(f"{'table':<20} {'institution':<14} {'n':>10}")
            for table, inst, n in rows:
                print(f"{table:<20} {inst:<14} {n:>10}")

            # Evidence body coverage
            cur.execute(
                """
                SELECT institution,
                       count(*) FILTER (WHERE body_md IS NOT NULL AND body_md <> '') AS with_body,
                       count(*) AS total
                FROM hackathon.evidence
                GROUP BY institution
                ORDER BY institution
                """
            )
            print("\nEvidence markdown coverage:")
            for inst, with_body, total in cur.fetchall():
                print(f"  {inst}: {with_body}/{total} have body_md")

    print("\nLoad complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
