-- Row counts per institution / table after a successful load.
-- Run: psql "$SUPABASE_DB_URL" -f scripts/supabase/verify.sql

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

-- Expected source totals (from JSONL line counts):
-- customers:         bank 1000, payments_fx 1000, emoney 1000, investment 1000  (=4000)
-- cases:             1000 each                                                    (=4000)
-- alerts:            bank 1687, payments_fx 1266, emoney 1503, investment 2011 (=6467)
-- transactions:      bank 78184, payments_fx 48313, emoney 72284, investment 10431 (=209212)
-- evidence:          bank 4233, payments_fx 4055, emoney 4151, investment 3963 (=16402)
-- previous_reviews:  bank 500, payments_fx 389, emoney 342, investment 297     (=1528)
-- internal_list:     bank 142, payments_fx 125, emoney 164, investment 169     (=600)
-- policies:          1 per institution                                             (=4)

SELECT institution,
       count(*) FILTER (WHERE body_md IS NOT NULL AND body_md <> '') AS with_body,
       count(*) AS total
FROM hackathon.evidence
GROUP BY institution
ORDER BY institution;

SELECT institution, count(*) AS n
FROM hackathon.case_alerts
GROUP BY institution
ORDER BY institution;

SELECT institution, count(*) AS n
FROM hackathon.alert_transactions
GROUP BY institution
ORDER BY institution;
