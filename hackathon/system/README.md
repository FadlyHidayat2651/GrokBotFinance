# AML triage

`./run` reads one institution folder and writes one JSON line per case: `CLOSE`, `RFI`, or `ESCALATE`, with a reason code from that verdict, a 2–4 sentence reason, and evidence ids that exist in the case.

```
./run /absolute/path/institutions/bank /absolute/path/output/bank.jsonl
```

The process does not use the current directory. It reads only the folder it was given. It writes only the output path and, when a model call is made, a cache file under `system/cache/`.

## How a case is decided

The pipeline is `loader.py` → `facts.py` → `analyze.py` → `decide.py` → `explain.py`.

`loader.py` joins the institution files. `facts.py` reads each document and pulls amounts, dates, account numbers, names, and table rows. `analyze.py` labels support chats and staff notes with fixed phrases (structuring, a refused corridor explanation, mule activity, third-party use, a returned cash withdrawal, a sale, a gift, a loan from a friend). A document type such as an invoice, register, or bank statement is not labeled this way. Keywords inside those papers are read by the decision engine directly.

`decide.py` never asks the model for a verdict. Order of decision:

1. Escalation signals that override a matching document. A fraud report or a mule admission, third parties investing through the account, a shared or foreign device together with rapid movement, a structuring admission, a refusal to explain a higher-risk corridor, two contradictory stories, a payment from a dissolved company that was moved on, a list match where the person is the same party or still controlled the payee (including a PSC who ceased on or after the payment), newly formed companies with the same director, and an invoice whose goods do not match the stated business.
2. Documented explanations. A list match on a different party, business records whose lines match the cash, a booking that names the travellers, a sale receipt, a returned withdrawal, a solicitor or insurer or completion or redundancy letter, a loan and a dealer invoice, a contract schedule, freight invoices, a payment file, an invoice or purchase order, a refund or credit note, a gift letter, a group chart, a factoring remittance, a cancellation, a tenancy deposit, an employment contract plus the customer's own statement, or a bank statement that shows the customer holds the other account.
3. Close only when every alert in the case is covered. Own-account transfers at or above 5 times the declared baseline become `RFI_UPDATED_PROFILE`. On payments_fx, a held payment of £10,000 or more to anyone other than the client stays `RFI_PURPOSE_DOCUMENTS` until an invoice or contract on file matches that payment.
4. Anything left over is an RFI when one document would settle it, and an escalation when it would not. Unexplained just-under-threshold payments are structuring. An unexplained higher-risk corridor is escalated. A list match that nothing settles is `RFI_COUNTERPARTY_DETAILS`.

Round amounts, a shared device on its own, and crypto on its own do not decide a case. An earlier review is not evidence and is not cited.

Thresholds and the 17 higher-risk countries are in `policy_config.json`. On startup the program compares that list with the folder's `POLICY.md` and prints a note on stderr if they differ. It does not stop.

## Model

Default provider is xAI, OpenAI-compatible, `https://api.x.ai/v1/chat/completions`, model `grok-4.7`, key `XAI_API_KEY`. If that key is unset and `OPENROUTER_API_KEY` is set, the call goes to `https://openrouter.ai/api/v1/chat/completions` with model `x-ai/grok-4.7`. `LLM_PROVIDER=grok` or `LLM_PROVIDER=openrouter` forces one of those when its key is present. Temperature is 0 and the seed is 1. The xAI API rejects a seed of 0. The HTTP client is `urllib` from the standard library.

When a key is set, every case makes one specialist-agent call. The agent fills seven investigation sentences and may add a suspicion: mule activity, structuring, a refused higher-risk explanation, or other people using the account. It cannot produce a `CLOSE`. A documented escalation is kept. If the deterministic result would close or ask the customer, and the agent reports one of those suspicions, the case is escalated instead. A failed call leaves the deterministic result. The response is cached under `system/cache/` by a hash of the request, so a repeat run does not call the network and cannot change the verdict or the reason code.

With no key, or with `DISABLE_LLM=1`, the phrase list and the document checks decide alone.

## Time and cost for 200 cases

With the model disabled, 1,000 bank cases took 0.7 seconds and the first 200 took 0.058 seconds after the folder was loaded. Cost was $0.

With the key set, every case makes one completion. A sample of 6 development cases all returned, at about 17 seconds each when run one after another. Four cases run at a time (`max_workers` in `policy_config.json`). A failed call keeps the deterministic result.

## Live page

`python3 live.py` serves a page at `http://127.0.0.1:8765/`. Each case is decided by the same path as `./run` and the row is sent when that case is finished, in `cases.jsonl` order. The output file written beside the run is the file `validate.py` checks. `CASE_LIMIT` on `./run` decides only the first N cases. Leave it unset for a full folder.

## Checks

`python3 validate.py <institution_folder> <output.jsonl>` checks that every case is present once, in order, with a reason code that belongs to the verdict, 2–4 sentences, and evidence ids that exist in that folder.
