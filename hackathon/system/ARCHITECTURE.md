# Solution architecture (update-v2)

Target verdicts: **CLOSE · RFI · ESCALATE**

This document describes how `hackathon/system` works after the surrounding-LLM
pass, and how it maps to Architect Final layers L1–L8.

## How a case is decided

```
institution folder
        │
        ▼
┌───────────────────┐
│ L1 loader.py      │  join cases, alerts, customers, txns, evidence, list
└─────────┬─────────┘
          ▼
┌───────────────────┐
│ L3 facts.py       │  deterministic document parse (amounts, dates, tables)
└─────────┬─────────┘
          ▼
┌───────────────────┐
│ analyze.py        │  phrase labels on chats/notes; optional LLM label assist
└─────────┬─────────┘
          ▼
┌───────────────────┐
│ L7 decide.py      │  FIRST PASS — policy / suspicion / explanations / residue
└─────────┬─────────┘
          │
          │  clear CLOSE / strong RFI / strong ESCALATE
          │ ─────────────────────────────────────────────► explain.py ► JSONL
          │
          │  weak residue (mainly ESC_UNEXPLAINED_FUNDS,
          │  RFI_SOURCE_OF_FUNDS, RFI_PURPOSE_DOCUMENTS)
          ▼
┌───────────────────────────────┐
│ Surrounding LLM               │  surrounding.py (optional)
│  • investigate only           │  temp 0 + disk cache
│  • label missed free text     │  never emits a verdict
│  • name concrete missing_info │
└───────────────┬───────────────┘
                ▼
┌───────────────────┐
│ L7 decide.py      │  SECOND PASS with any new labels
└─────────┬─────────┘
          │
          │  still ESC_UNEXPLAINED + missing_info?
          ▼
┌───────────────────┐
│ Safe RFI ask      │  downgrade unexplained escalate → RFI (never CLOSE)
└─────────┬─────────┘
          ▼
┌───────────────────┐
│ L8 explain.py     │  2–4 sentences + validated evidence IDs
└─────────┬─────────┘
          ▼
     output JSONL
```

## Hard rules

| Rule | Meaning |
|---|---|
| Decision engine owns the verdict | `decide.py` produces CLOSE / RFI / ESCALATE |
| LLM never decides | Surrounding + analyze return labels / findings / asks only |
| No key / LLM fail / `DISABLE_LLM` / `DISABLE_SURROUNDING` | Deterministic path only; system still runs |
| Wrongful CLOSE is expensive | Surrounding may turn unexplained escalate into RFI; it must not invent CLOSE |
| Reproducibility | Temperature 0, seed 0, cache by request hash under `system/cache/` |
| No Supabase at runtime | Submit path reads the institution folder only |

## Mapping to Architect Final

| Layer | In this codebase | Status |
|---|---|---|
| L1 Data integration | `loader.py` + `facts.build_case` | Present (in-memory case object) |
| L2 Policy parser | `policy_config.json` + `config.parse_policy` | Partial — thresholds, not clause-ID packs yet |
| L3 Deterministic facts | `facts.py` | Present; alert-type checklist still mostly inside `decide.py` |
| L4 Specialist agents | Logic embedded in `decide.py` / `analyze.py` | Not split into agent modules yet |
| L5 Case evidence dossier | Case dict + optional `case["surrounding"]` | Lightweight; not a separate artefact store |
| L6 CLOSE skeptic | `_gates` + suspicion-first ordering | Partial; not a standalone `skeptic.py` |
| L7 Decision engine | `decide.py` | Present (core); update-v2 hardens TM22, FX held-invoice, employer bonus, mule codes |
| L8 Explanation | `explain.py` | Present |
| Surrounding LLM | `surrounding.py` | **New in update-v2** — residual / fail assist |
| Supervised ML | — | Disabled (no gold labels) |

## Synthetic regression (testdata)

Hand-labeled `testdata/` (`test_bank` + `test_payments_fx`, 13 cases) is the local policy regression gate:

```bash
DISABLE_LLM=1 ./run /ABS/testdata/institutions/test_bank /tmp/test_bank.jsonl
DISABLE_LLM=1 ./run /ABS/testdata/institutions/test_payments_fx /tmp/test_payments_fx.jsonl
python3 /ABS/testdata/score_test.py \
  --expected /ABS/testdata/expected.jsonl \
  --data /ABS/testdata \
  --output /tmp/test_bank.jsonl /tmp/test_payments_fx.jsonl
```

Target: 13/13 verdicts, 0 wrongful CLOSE, schema clean.

## Surrounding trigger set

The second pass runs only when the first pass reason code is one of:

- `ESC_UNEXPLAINED_FUNDS`
- `RFI_SOURCE_OF_FUNDS`
- `RFI_PURPOSE_DOCUMENTS`

Environment knobs:

| Variable | Effect |
|---|---|
| `XAI_API_KEY` or `OPENROUTER_API_KEY` | Enables model calls |
| `DISABLE_LLM=1` | No analyze LLM and no surrounding |
| `DISABLE_SURROUNDING=1` | Keep analyze LLM assist; skip surrounding only |
| `LLM_PROVIDER=grok` / `openrouter` | Force provider when both keys exist |

## Deliverable contract (unchanged)

```
./run <institution_folder> <output.jsonl>
```

One JSON line per case: `case_id`, `alert_ids`, `verdict`, `reason_code`, `reason`, `evidence`.

## Intended next gaps (not in this PR)

1. Executable policy packs with clause IDs (L2)
2. Explicit dossier artefact (L5)
3. Alert-family required-facts checklist (L3)
4. Standalone CLOSE skeptic module (L6)
5. Split specialist agent modules (L4)
6. Eval suite: 5× determinism, policy fixtures, wrongful-CLOSE proxy
