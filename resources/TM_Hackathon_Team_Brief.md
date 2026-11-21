# Fin Crime Hackathon: Transaction Monitoring Track

Team brief, problem statement, data definition and build plan

*Status as of 21 November 2026. Team of four. Track chosen: Transaction Monitoring (alert triage).*

---

## 1. Context and decisions so far

| Topic | Decision |
|---|---|
| Hackathon | Diligent Fin Crime Hackathon. Two tracks were reviewed: Transaction Monitoring (TM) and Sanctions Screening |
| Track chosen | **TM: alert triage.** Sanctions Screening (is this listed person my customer?) was reviewed but not chosen |
| Earlier idea | Scam-proof banking / voice scam detection (PaySim, ASVspoof, Whisper, PhishTank). Parked: PaySim is account takeover rather than APP fraud, and nothing links calls to payments |
| LLM | Grok (xAI). Grok Premium gives app and Grok Build access only. **Code needs an API key and credits from console.x.ai** |
| Approach | Hybrid: rules from the policy + ML + a small multi-agent layer on Grok |
| Team | 4 people: ML, multi-agent, and two policy owners (or one policy owner plus front end if there is a demo round) |

---

## 2. Problem statement

Banks, payment firms and investment platforms must monitor customer transactions for money laundering, fraud proceeds and terrorist financing, and report suspicion to the National Crime Agency. Automatic rules raise many alerts. Most have an ordinary explanation (a bonus, a car sale, a supplier invoice), but every alert must still be reviewed by an analyst. That review is slow, costly and inconsistent.

**The challenge:** build a system that does the analyst's triage. It reads each alert with its evidence and decides how to close it, as carefully and consistently as a good analyst would.

> **One sentence for the team:** we are building an AI analyst that reads each flagged case and its evidence, applies the institution's rulebook, and decides to close, ask or escalate, with a clear reason and the evidence IDs it relied on. It must be accurate, explainable and give the same answer every time.

### The three verdicts

| Verdict | Meaning |
|---|---|
| **CLOSE** | The evidence explains every alert in the case and fits what the institution knows about the customer. At the payments and FX firm, a held payment is released |
| **RFI** | Request for information. Nothing is suspicious yet, and one specific document or answer from the customer would settle it. The reason must say what to ask for |
| **ESCALATE** | Send to the MLRO (Money Laundering Reporting Officer). The activity is unexplained or there are signs of crime |

Key rule: **once there is suspicion, escalate rather than RFI**, because asking the customer risks tipping them off, which is an offence.

One case = one customer over the review window (at payments and FX: one held outgoing payment). One case gets one verdict even when several rules fired. Close only when the evidence explains **every** alert in the case.

---

## 3. What we build

A runnable system that reads a folder of cases and writes one verdict per case.

```
system/run institutions/bank output/bank.jsonl
```

### Output: one JSON line per case

```json
{"case_id": "BK-CS112964",
 "alert_ids": ["BK-AL4810357"],
 "verdict": "CLOSE",
 "reason_code": "LIST_MATCH_DIFFERENT_PARTY",
 "reason": "The joinery firm's director shares the listed person's name, but the company register gives a birth year of 1993, not 1972. The £3,150.72 payment matches the firm's invoice for fitted wardrobes.",
 "evidence": ["REG-5461", "INV-7291", "BK-L37007"]}
```

| Field | Type | Holds |
|---|---|---|
| case_id | ID | The case, from cases.jsonl |
| alert_ids | list | Every alert in the case |
| verdict | enum | CLOSE, RFI or ESCALATE |
| reason_code | enum | Main ground for the verdict. Must belong to the verdict |
| reason | text | 2 to 4 sentences naming this case's own facts, documents, people and amounts. For an RFI, exactly what to ask for |
| evidence | list | IDs of documents, transactions or list entries relied on, exactly as they appear in the files |

### Hard constraints

- Test set: **800 unseen cases** (200 per institution), same format, same policies. The system runs the **whole pipeline** on them, including document reading.
- Nothing from the development cases (IDs, names, amounts) may be built into the system.
- Reads only the folder it is given, writes only its output file. Must not depend on the working directory.
- **200 cases within 30 minutes** (about 9 seconds per case).
- A case missing from the output counts as an escalation.

### Scoring

Cost per case (lower is better):

| Right verdict | We sent CLOSE | We sent RFI | We sent ESCALATE |
|---|---|---|---|
| CLOSE | 0 | 3 | 3 |
| RFI | **10** | 0 | 2 |
| ESCALATE | **10** | 3 | 0 |

| Part | Weight | What counts |
|---|---|---|
| Unseen cases | 60% | Verdicts 60%, reasons 40% |
| Development cases | 20% | Verdicts 60%, reasons 40% |
| Reproducibility | 20% | Same verdict and reason code in all 5 runs |

Implications:
- A wrongful close costs 10. Only close when roughly **more than 77% confident** it is closable.
- Between RFI and ESCALATE when unsure, escalate is slightly cheaper.
- Escalating everything scores 0 on verdicts.
- Reasons count for 40%: a correct verdict with a generic reason scores little.
- Runs must be deterministic.

---

## 4. Data definition

Location: `~/Downloads/hackathon/` (148 MB). Every customer, company and document is invented.

```
hackathon/
  README.md, BRIEF.html
  institutions/
    bank/  payments_fx/  emoney/  investment/
      POLICY.md
      cases.jsonl  alerts.jsonl  customers.jsonl  transactions.jsonl
      evidence.jsonl  evidence/*.md
      previous_reviews.jsonl  internal_list.jsonl
  system/   (we write: run executable + README.md)
  output/   (we write: bank.jsonl, emoney.jsonl, investment.jsonl, payments_fx.jsonl)
```

### 4.1 The four institutions

| Institution | Customers | Data | Rules |
|---|---|---|---|
| Bank | Individuals (85%) and small businesses (15%) | Cash at branches and ATMs, transfers, card spend | TM01 to TM07, TM22 (8) |
| Payments and FX | Businesses only, paying suppliers and staff abroad | Funding from own bank, SWIFT payments in 13 currencies, invoices, payment files. An alert holds the outgoing payment | TM01 to TM04, TM06, TM07, TM08, TM22 (8) |
| E-money | Individuals with app accounts | Bank top-ups, cash top-ups at shops, wallet transfers, card, ATM | TM01 to TM07, TM22 (8) |
| Investment | Individual investors | Deposits, withdrawals, fund subscriptions and redemptions | TM01, TM02, TM06, TM07, TM08, TM10, TM22 (7) |

### 4.2 Row counts

| File | Bank | Payments/FX | E-money | Investment | Total |
|---|---|---|---|---|---|
| cases | 1,000 | 1,000 | 1,000 | 1,000 | 4,000 |
| alerts | 1,687 | 1,266 | 1,503 | 2,011 | 6,467 |
| customers | 1,000 | 1,000 | 1,000 | 1,000 | 4,000 |
| transactions | 78,184 | 48,313 | 72,284 | 10,431 | 209,212 |
| evidence (index and .md files) | 4,233 | 4,055 | 4,151 | 3,963 | 16,402 |
| previous_reviews | 500 | 389 | 342 | 297 | 1,528 |
| internal_list | 142 | 125 | 164 | 169 | 600 |
| POLICY.md | 1 | 1 | 1 | 1 | 4 |

**The development cases have no answers (no labels).**

### 4.3 Tables ranked by number of columns

| Rank | Table | Top-level columns | Flattened columns | Rows |
|---|---|---|---|---|
| 1 | alerts | 9 | 40 | 6,467 |
| 2 | customers | 14 | 19 | 4,000 |
| 3 | transactions | 14 | 14 | 209,212 |
| 4 | internal_list | 8 | 9 | 600 |
| 5 | cases | 7 | 8 | 4,000 |
| 6 | evidence (index) | 6 | 6 | 16,402 |
| 7 | previous_reviews | 6 | 6 | 1,528 |
| | **Total** | **64** | **102** | **about 242,000** |

### 4.4 Table schemas

#### cases.jsonl (the unit of work)

| Column | Type | Notes |
|---|---|---|
| case_id | str | PK, e.g. BK-CS100112 |
| institution | str | bank, payments_fx, emoney, investment |
| customer_id | str | FK to customers |
| opened_at | datetime | |
| review_window.from / .to | date | The review period |
| alert_ids | list[str] | FK to alerts. Usually 1 to 2 per case |
| held_payment_id | str or null | Payments/FX only (51% of its cases). FK to transactions |

#### alerts.jsonl (what was flagged)

| Column | Type | Notes |
|---|---|---|
| alert_id | str | PK, e.g. BK-AL2450697 |
| case_id, customer_id | str | FKs |
| rule_id, rule_name | str | TM01 to TM22 |
| created_at | datetime | |
| message | text | Plain English summary of why it fired |
| measured | JSON | Rule specific metrics, see below |
| transaction_ids | list[str] | FK to transactions the alert fired on |

`measured` fields by rule:

| Rule | Fields |
|---|---|
| TM01 Activity exceeds profile | received_gbp, baseline_gbp, multiple, limit_multiple, window_days |
| TM02 Rapid movement of funds | received_gbp, paid_out_gbp, share, limit_share, invested_gbp, window_days |
| TM03 Repeated below threshold | count, limit_count, total_gbp, locations, beneficiary, payments, window_days |
| TM04 Many-to-one / one-to-many | direction, distinct_counterparties, limit, total_gbp, window_days |
| TM05 Large cash | amount_gbp, limit_gbp |
| TM06 Higher-risk corridor | countries, total_gbp, limit_gbp, window_days |
| TM07 Dormant reactivation | dormant_days, limit_days, moved_gbp, limit_gbp |
| TM08 Third-party funding/payout | flows, total_gbp, limit_gbp |
| TM10 Early fund redemption | fund, held_days, limit_days, bought_gbp, redeemed_gbp |
| TM22 Internal list match | list_entry (FK to internal_list), matched_on, matched_party |

Alert mix: TM01 is the most common everywhere. TM08 is large at Payments/FX (299) and Investment (585).

#### customers.jsonl (who the customer is, what is normal)

| Column | Type | Notes |
|---|---|---|
| customer_id | str | PK |
| kind | str | individual or business |
| name, address | str | |
| date_of_birth, occupation, employer | str or null | Individuals only |
| company_number, incorporated_on, business_activity | str or null | Businesses only |
| onboarded_on | date | |
| risk_rating | str | Almost always Low, weak signal |
| account_ids | list[str] | |
| declared.source_of_funds | str | e.g. Salary, Business revenue |
| declared.account_purpose | str | |
| declared.expected_countries | list[str] | Baseline for TM06 |
| declared.expected_channels | list[str] | |
| declared.monthly_income_gbp | number or null | Individuals. Baseline for TM01 |
| declared.annual_turnover_gbp | number or null | Businesses. Baseline for TM01 |

#### transactions.jsonl (what happened)

| Column | Type | Notes |
|---|---|---|
| transaction_id | str | PK |
| customer_id, account_id | str | FKs |
| booked_at | datetime | Usually last 120 days, longer for dormant accounts |
| direction | str | in or out |
| channel | str | card, direct_debit, bacs, faster_payment, standing_order, cash_deposit, cash_withdrawal, cash_topup, wallet_transfer, swift, fund_subscription, fund_redemption |
| amount, currency | number, str | Original currency |
| amount_gbp | number | Always in pounds |
| counterparty_name | str | Mostly filled |
| counterparty_account | str or null | Missing 36% to 95% |
| counterparty_country | str | Higher-risk seen: Lebanon, Haiti, Iraq (and UAE, Turkey as other foreign) |
| reference | str | Short text, e.g. FUNDING, tickets |
| location | str or null | Cash only |

#### evidence.jsonl (index of documents on file)

| Column | Type | Notes |
|---|---|---|
| doc_id | str | PK, e.g. INV-7291, KYC-6179 |
| customer_id | str | FK |
| type | str | Document type, see 4.5 |
| title | str | |
| dated | date | |
| file | str | Path to evidence/<doc_id>.md |

#### previous_reviews.jsonl (history)

| Column | Type | Notes |
|---|---|---|
| review_id | str | PK |
| customer_id | str | FK |
| reviewed_on | date | |
| rule_id | str | |
| verdict | str | Always CLOSE |
| note | text | Why it was closed, e.g. "Annual bonus paid by the employer; payslip on file." Covers only what it describes |

#### internal_list.jsonl (firm's own watchlist, for TM22)

| Column | Type | Notes |
|---|---|---|
| entry_id | str | PK, e.g. BK-L37007 |
| kind | str | individual or business |
| name | str | |
| date_of_birth | str or null | |
| address | str or null | Missing 95% at Investment |
| identifiers.internal_case, identifiers.swift | str | |
| reason | text | e.g. earlier internal case, exited customer, blocked bank |
| added_on | date | |

### 4.5 Text data

**Evidence documents: 16,402 Markdown files.** Layout: a title, fixed fields (`**Field:** value`), then free text.

| Group | Approx. count | Types | Role |
|---|---|---|---|
| Who the customer is | 8,800 | due_diligence_summary (KYC, one per customer), proof_of_address, company_register_extract, significant_control_register, group_structure, reverification_record | Verified profile, owners and officers |
| Conversations and notes | 4,400 | contact_note, support_chat, staff_note, email, customer_email, customer_letter, venue_email | What the customer said (may or may not be true) |
| What a payment was for | 1,200 | invoice, supplier_invoice(s), dealer_invoice, hospital_invoice, carrier/freight invoices, receipt, payment_file, purchase_order, credit_note, remittance_advice, loan/consulting/supply/employment/factoring contracts | Explain outgoing payments |
| Where money came from | 1,000 | bank_statement, gift_letter, insurer_letter, solicitor_letter, settlement_letter, redundancy_letter, completion_statement, sale and vehicle sale receipts, booking_confirmation, deposit_return_letter, advert | Explain incoming money, prove own accounts |
| Business records | 250 | management_accounts, takings_book, cash_collection_reports, cash_reconciliation, cash_handling_procedure, ticket_report | Explain business cash |
| Warning signs | 240 | sending_bank_fraud_report (156: bank, e-money), device_export (84: e-money) | Signals of mule activity or account misuse |

Distinctive by institution:
- Bank: fraud reports, cash collection and reconciliation, vehicle sale receipts
- Payments/FX: supplier invoices, payment files, group structures, factoring agreements
- E-money: device exports, fraud reports, takings books, holding deposits
- Investment: gift letters (140), completion statements, redundancy letters

Example (`evidence/RPT-1148.md`, bank):

```markdown
# Fraud report from Fairhaven Bank
**Document ID:** RPT-1148
**Dated:** 15 September 2026
**From:** Fraud operations, Fairhaven Bank
**Subject:** Authorised push payment scam: request to freeze funds

Our customer Harper Glenford sent £410.00 on 14 September 2026 to your
customer's account ending 2064, reference "payment". They tell us they paid
a deposit on a used car and the seller then stopped replying...
```

**POLICY.md: 4 files (about 1,400 words each).** The institution's SOP: verdicts, what a case is, rule limits, policy settings, reason codes.

**Free-text columns inside tables:** alerts.message, previous_reviews.note, internal_list.reason, alerts.measured.matched_party, transactions.reference, customers.business_activity.

### 4.6 Structured vs text

| Data | Type | Processing |
|---|---|---|
| cases, alerts, customers, transactions, evidence index, previous_reviews, internal_list | Structured (RDBMS) | Load into DuckDB or Postgres. Flatten review_window, declared, identifiers. Keep measured as JSON. Bridge tables for list columns |
| KYC summaries, invoices, register extracts | Semi-structured text | Parse fixed fields with code |
| Chats, notes, letters, emails, fraud reports, device exports | Unstructured text | LLM extraction to JSON facts |
| POLICY.md | Text rulebook | Translate into rules and agent prompts |

### 4.7 How the data answers the analyst's questions

| Question | Data |
|---|---|
| What was flagged? | cases, alerts |
| Who is the customer, what is normal? | customers (declared), KYC summary, company registers |
| What happened? | transactions |
| Is there an explanation? | evidence documents |
| Any warning signs or history? | fraud reports, device exports, internal_list, previous_reviews |
| What are the rules? | POLICY.md |

> Alert (what is odd) vs customer (what is normal), then transactions (what happened), then evidence (why), then warning signs and history, then policy, then verdict.

### 4.8 Join keys

```
cases.alert_ids            -> alerts.alert_id
cases.customer_id          -> customers.customer_id
cases.held_payment_id      -> transactions.transaction_id
alerts.transaction_ids     -> transactions.transaction_id
alerts.measured.list_entry -> internal_list.entry_id
customer_id                -> transactions, evidence, previous_reviews
evidence.file              -> evidence/<doc_id>.md
```

### 4.9 Policy reference: rule limits

| Rule | Bank | Payments/FX | E-money | Investment |
|---|---|---|---|---|
| TM01 Profile | 30-day inflow above 3x monthly income (individual) or 2x monthly turnover (business) | Above 2x monthly share of declared annual payment volume | Same as bank | Same as bank |
| TM02 Rapid movement | 80% of £2,000+ received paid out within 48h, over 10 days | Same, but only money from anyone other than the client | Same as bank | 80% withdrawn within 10 days and less than 20% invested |
| TM03 Below threshold | 3+ cash deposits of £9,000 to £9,999.99 in 10 days | 3+ payments to one beneficiary of £9,000 to £9,999.99 in 10 days | 4+ cash top-ups of £800 to £999.99 in 10 days | Does not run |
| TM04 Many-to-one | More than 6 payers or payees in 30 days | More than 25 beneficiaries in 30 days | More than 6 | Does not run |
| TM05 Large cash | £10,000+ individual, £25,000+ business | No cash | £1,000+ individual, £5,000+ business | No cash |
| TM06 Higher-risk country | £5,000+ in 30 days, undeclared country | Same | Same | Same |
| TM07 Dormant | £5,000+ after 180 days inactive | Same | Same | Same |
| TM08 Third party | Does not run | Funding of £1,000+ from an account not in the client's name | Does not run | Deposit or withdrawal of £1,000+ from or to someone else |
| TM10 Early redemption | Does not run | Does not run | Does not run | Units sold within 90 days of purchase |
| TM22 Internal list | Payer, recipient or company officer matches by name, address, DOB or identifier. One detail alone does not prove it is the same party | Same | Same | Same |

### 4.10 Policy settings (all institutions unless stated)

- Higher-risk countries (as of 1 Oct 2026): Afghanistan, Belarus, DR Congo, Haiti, Iran, Iraq, Lebanon, Libya, Mali, Myanmar, North Korea, Russia, Somalia, South Sudan, Syria, Venezuela, Yemen.
- **Own accounts:** count only when a document on file shows the customer holds the other account. If such transfers bring activity to 5x declared income or volume or more, RFI for an updated profile. Below 5x, close.
- **Internal list:** close only when evidence shows a different party. If nothing settles it and nothing else is concerning, RFI. If the listed party is the counterparty or controls it, escalate.
- **Earlier reviews** cover only what they describe, never new or larger activity.
- **Payments/FX only:** payments of £10,000+ to anyone other than the client need an invoice or contract on file before release.
- **Context, not triggers:** round amounts, shared device, IP or contact details, a recipient added the same day, crypto or gambling counterparties.

### 4.11 Reason codes

| Verdict | Code | Use when |
|---|---|---|
| CLOSE | COLLECTION_DOCUMENTED | A documented collection on behalf of named people explains the payments |
| CLOSE | BUSINESS_RECORDS_RECONCILE | Takings or collection reports reconcile to the activity |
| CLOSE | DOCUMENTED_EVENT | A documented one-off event (sale, inheritance, payout) explains the funds |
| CLOSE | INVOICE_MATCHES | An invoice, contract or payment file matches amount, payee and date |
| CLOSE | OWN_ACCOUNT | Money moved between accounts the customer is shown to hold |
| CLOSE | GROUP_COMPANY | Counterparty is a verified company in the same group |
| CLOSE | THIRD_PARTY_DOCUMENTED | A documented relationship explains a third-party payer |
| CLOSE | REFUND_OF_EARLIER_PAYMENT | The money is a refund of an earlier payment |
| CLOSE | RETURNING_CUSTOMER | A verified customer resumed using a dormant account |
| CLOSE | LIST_MATCH_DIFFERENT_PARTY | Evidence shows the counterparty is not the listed party |
| CLOSE | WITHIN_PROFILE | Activity fits the declared profile or instructions |
| RFI | RFI_PURPOSE_DOCUMENTS | Ask for documents showing what payments were for |
| RFI | RFI_BUSINESS_RECORDS | Ask for business records behind the activity |
| RFI | RFI_SOURCE_OF_FUNDS | Ask for proof of where the money came from |
| RFI | RFI_COUNTERPARTY_DETAILS | Ask who the counterparty is and what the payment was for |
| RFI | RFI_UPDATED_PROFILE | Ask for an updated expected income or volume |
| ESCALATE | ESC_MULE_ACTIVITY | Account receives and passes on other people's money |
| ESCALATE | ESC_STRUCTURING | Transactions split to stay under a threshold |
| ESCALATE | ESC_UNEXPLAINED_FUNDS | No credible explanation and the money was moved on |
| ESCALATE | ESC_PASS_THROUGH | Money passes through with no credible purpose |
| ESCALATE | ESC_INVOICE_MISMATCH | Invoices do not support the payments |
| ESCALATE | ESC_HIGH_RISK_CORRIDOR | Unexplained payments to or from a higher-risk country |
| ESCALATE | ESC_ACCOUNT_MISUSE | Someone other than the customer appears to control the account |
| ESCALATE | ESC_THIRD_PARTY_FUNDING | Unrelated third parties fund or receive money |
| ESCALATE | ESC_EARLY_REDEMPTION | Fund units bought and quickly sold back to move money |
| ESCALATE | ESC_LISTED_PARTY | Counterparty is the listed party or controlled by it |

---

## 5. Solution architecture

### 5.1 Pipeline

```
case folder
  ├── Load and join tables (DuckDB, by customer_id)          [code]
  └── Extract facts from documents -> doc_facts table        [code for fixed layouts, Grok for free text, temp 0, cached]
                   │
        Fixed policy rules (must escalate / must RFI / clear closes)
                   │
        ML model -> verdict + confidence
           │ confident                │ unsure
        Skeptic check          Grok policy analyst -> Skeptic check
                   │
        Reason writer (templates from facts) -> Validator -> output/*.jsonl
```

Example `doc_facts` rows:

| doc_id | type | amount_gbp | date | party | counterparty | relationship | flags |
|---|---|---|---|---|---|---|---|
| INV-1159 | invoice | 3450.00 | 2026-08-08 | Bailey Falhurst | Elmden Consulting Ltd | | |
| RPT-1148 | fraud_report | 410.00 | 2026-09-14 | Harper Glenford | account ending 2064 | victim | APP_SCAM |

### 5.2 Machine learning

| Model | Purpose |
|---|---|
| Verdict classifier (LightGBM/XGBoost) | CLOSE / RFI / ESCALATE with confidence. Features from alerts.measured, customers.declared, transaction aggregates, document facts (about 40 to 60 features) |
| Reason code classifier | Picks the code within the chosen verdict |
| Matchers | Invoice to transaction (amount, payee, date gap); internal list to counterparty (name, DOB, address similarity) |
| Anomaly detector (Isolation Forest) | Unsupervised "how unusual" score per customer as an extra feature |

**Labels:** none provided. Grok labels the development cases, the policy owners hand check and correct them (target 100+ per institution), and the ML model is trained on those (distillation: Grok's judgement in a fast, deterministic model). Hard policy rules always override ML.

### 5.3 Multi-agent (modelled on a real compliance team)

| Agent | Role | Runs on |
|---|---|---|
| Orchestrator | Routes each case through fixed steps | Code |
| Evidence reader | Documents to facts JSON | Grok |
| Transaction analyst | Links flagged transactions to documents, checks amounts and dates | Code + matchers |
| Policy analyst | Applies POLICY.md to unclear cases | Grok |
| Skeptic (four-eyes checker) | Looks for reasons not to close: fraud reports, mismatches, earlier review not covering new activity, tipping-off risk | Grok |
| Reason writer | 2 to 4 sentences citing real IDs and amounts | Templates |
| Validator | Cited IDs exist, reason code fits verdict, all alerts covered | Code |

### 5.4 Reproducibility and speed

- Temperature 0, strict JSON output, fixed enums for Grok.
- Cache document facts by content hash.
- Decisions made by code and ML, not by free LLM text.
- Run cases in parallel to stay within 30 minutes per 200 cases.

---

## 6. Team roles

| Person | Builds | Also owns |
|---|---|---|
| 1. ML | Verdict and reason models, matchers, anomaly score | Data loader, case bundles, scorer (cost matrix and 5-run reproducibility check) |
| 2. Multi-agent | Orchestrator, Grok agents, Skeptic, reason writer | system/run, output writer, validator, Ollama fallback |
| 3. Policy A | Rules for bank and e-money | Hand labels and reason templates for both |
| 4. Policy B | Rules for payments/FX and investment | Hand labels and reason templates for both |

Option if there is a demo round: person 4 builds a Streamlit analyst console (case queue, verdict and confidence, cited documents, Skeptic objections, summary dashboard) and the pitch, starting only after the baseline runs. Person 3 then covers all four policies with Grok drafting labels. The front end is **not scored**.

Interfaces to agree in the first hour:
1. Case bundle format (person 1)
2. doc_facts format (person 2)
3. `decide(bundle, facts)` returning verdict, reason_code, reason, evidence (persons 3 and 4)

---

## 7. Plan

| Phase | Goal |
|---|---|
| 0. Start | Everyone reads all four POLICY.md files. Agree interfaces. Each person labels 20 cases |
| 1. Baseline | End-to-end run with valid output, even if it escalates everything |
| 2. Build | Clear-case rules, Grok extraction, labels to 50 to 100 per institution |
| 3. Improve | ML and Grok for unclear cases, tune thresholds against the cost table |
| 4. Freeze | 5-run reproducibility test, 30-minute timing check, README, demo |

Rule for everyone: run the scorer after every change.

---

## 8. Open questions for the organisers

1. Are external API calls (Grok) allowed during the hidden test run? If not, use a local model via Ollama or code-only parsing.
2. Is there a demo or pitch round, and does it count?
3. Any compute or runtime environment details for `system/run` (Python version, packages)?

## 9. Grok setup notes

- Grok Premium (app) does not include API access. Create a key at console.x.ai.
- Check whether any redeemed credits appear under Billing, API Credits.
- Keep the invoiced billing limit at $0 so requests stop when prepaid credits run out.
- Keep the key in `.env`, list it in `.gitignore` and `.cursorignore`, and never commit it.
