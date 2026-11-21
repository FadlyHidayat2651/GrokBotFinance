# TM Hackathon: Data Analysis

*First pass over the development data in `hackathon/institutions/`, 21 November 2026.*

## 1. Summary

- **The data is clean.** All joins resolve in all four institutions: alerts, transactions, held payments, internal list entries and evidence files.
- **The data is template-generated.** If you group cases by *rule combination + non-filler document types*, each institution has only **30–35 case signatures**, about 128 scenario templates in total. The 800 unseen cases come from the same generator, so expect about 6 cases per signature per institution.
- **The verdict looks fixed within a signature.** In both signatures tested, every case had the same deciding detail. Section 7 has the details.
- **Filler documents never decide a case.** The KYC summary, proof of address and contact note sit on almost every customer. Contact notes use only 6 distinct routine texts.
- **Recommended approach:** a deterministic rule engine keyed on the scenario, which runs the deciding check (amounts, dates, names, control windows). An LLM at temperature 0 through OpenRouter would only classify the paraphrased chats and notes. No ML: there are no labels to train on, and the scenario space is small.

## 2. Row counts

| File | Bank | Payments/FX | E-money | Investment |
|---|---|---|---|---|
| cases | 1,000 | 1,000 | 1,000 | 1,000 |
| alerts | 1,687 | 1,266 | 1,503 | 2,011 |
| customers | 1,000 | 1,000 | 1,000 | 1,000 |
| transactions | 78,184 | 48,313 | 72,284 | 10,431 |
| evidence | 4,233 | 4,055 | 4,151 | 3,963 |
| previous_reviews | 500 | 389 | 342 | 297 |
| internal_list | 142 | 125 | 164 | 169 |

Broken joins: **0** in every institution (case→alert, alert→transaction, held payment→transaction, TM22→list entry, evidence→file).

## 3. Cases and alerts

| | Bank | Payments/FX | E-money | Investment |
|---|---|---|---|---|
| Alerts per case (1 / 2 / 3) | 384 / 545 / 71 | 759 / 216 / 25 | 586 / 325 / 89 | 270 / 449 / 281 |
| Cases with held payment | 0 | 489 | 0 | 0 |

Rule mix (alert counts):

| Rule | Bank | Payments/FX | E-money | Investment |
|---|---|---|---|---|
| TM01 Profile | 651 | 317 | 456 | 669 |
| TM02 Rapid movement | 330 | 157 | 389 | 225 |
| TM03 Below threshold | 113 | 107 | 87 | – |
| TM04 Many-to-one | 111 | 94 | 132 | – |
| TM05 Large cash | 149 | – | 46 | – |
| TM06 High-risk country | 112 | 121 | 132 | 141 |
| TM07 Dormant | 112 | 75 | 133 | 128 |
| TM08 Third party | – | 299 | – | 585 |
| TM10 Early redemption | – | – | – | 132 |
| TM22 Internal list | 109 | 96 | 128 | 131 |

## 4. Customers and transactions

| | Bank | Payments/FX | E-money | Investment |
|---|---|---|---|---|
| Kind | 851 individual, 149 business | 1,000 business | 1,000 individual | 1,000 individual |
| Risk rating | 964 Low, 36 Medium | all Low | all Low | all Low |
| Source of funds | Salary 816, Business revenue 149, Student loan/parents 35 | Business revenue 1,000 | Salary 954, Self-employment 46 | Salary 1,000 |

Note: the e-money policy mentions small businesses, but every e-money customer in the development data is an individual.

Transaction channels:

| Institution | Channels |
|---|---|
| Bank | card 46,731, direct_debit 10,059, bacs 9,032, faster_payment 5,121, standing_order 3,994, cash_deposit 2,460, swift 402, cash_withdrawal 385 |
| Payments/FX | swift 29,999, faster_payment 18,314 |
| E-money | card 48,518, faster_payment 11,217, wallet_transfer 7,330, cash_withdrawal 4,326, cash_topup 590, swift 303 |
| Investment | faster_payment 5,578, fund_subscription 4,430, fund_redemption 232, swift 191 |

Higher-risk countries in the data: **Lebanon, Haiti** (bank, e-money), **Iraq, Lebanon** (investment). The UAE and Turkey also appear as other foreign countries. Payments/FX pays to many countries (US, China, Spain, Poland, Vietnam, …).

## 5. Evidence documents

### 5.1 Filler vs signal

| Type | Present on | Role |
|---|---|---|
| `due_diligence_summary` | Every customer | KYC profile: mirrors `customers.jsonl` declared fields |
| `proof_of_address` | Every individual | Energy bill |
| `contact_note` | ~80–90% of customers | **Noise.** Only 6 distinct texts across all institutions: lost card, paper statements, standing order help, charges query, mobile number update, address confirmed |

All other document types are scenario-specific and carry the deciding evidence.

### 5.2 Document types per institution

| Institution | Types | Scenario-specific types (count) |
|---|---|---|
| Bank | 27 | company_register_extract 330, support_chat 291, bank_statement 149, sending_bank_fraud_report 72, and about 39 each of: booking_confirmation, cash_collection_reports, cash_handling_procedure, cash_reconciliation, insurance_schedule, insurer_letter, vehicle_sale_receipt, solicitor_letter, venue_email, dealer_invoice, hospital_invoice, loan_agreement, receipt, reverification_record, ticket_report, management_accounts, consulting_contract, staff_note, invoice, significant_control_register |
| Payments/FX | 24 | company_register_extract 1,146, bank_statement 210, supplier_invoice 111, supplier_invoices 84, invoice 84, staff_note 84, payment_file 63, credit_note 45, group_structure 44, significant_control_register 42, consulting_contract 41, supply_contract 34, contract_schedule 34, purchase_order 33, and 22 each of: email, factoring_agreement, insurer_letter, carrier_invoice, freight_invoices, receipt, remittance_advice, reverification_record |
| E-money | 23 | bank_statement 180, support_chat 170, company_register_extract 128, device_export 84, sending_bank_fraud_report 84, and about 45 each of: advert, insurer_letter, reverification_record, sale_receipt, takings_book, booking_confirmation, venue_email, hospital_invoice, holding_deposit_receipt, deposit_return_letter, receipt, settlement_letter, invoice, significant_control_register, staff_note |
| Investment | 15 | bank_statement 262, support_chat 200, gift_letter 140, customer_email 91, company_register_extract 50, and about 46 each of: completion_statement, insurer_letter, customer_letter, redundancy_letter, reverification_record, cancellation_confirmation, employment_contract |

### 5.3 Document format

Each document has a `# Title`, `**Document ID:**`, `**Dated:**`, then either fixed `**Field:** value` lines or Markdown tables, then free text.

| Parse with | Types |
|---|---|
| Regex on fixed fields | invoice, supplier_invoice, dealer_invoice, hospital_invoice, carrier_invoice, consulting_contract, credit_note, remittance_advice, bank_statement, booking_confirmation, insurance_schedule, vehicle_sale_receipt, completion_statement, management_accounts, company_register_extract, due_diligence_summary, proof_of_address |
| Markdown table | significant_control_register (Person, Born, Control, Notified, Ceased), company_register_extract officers (Officer, Role, Born, Appointed, Resigned), group_structure (Company, Owner, Share), device_export (Device, First seen, Customers, Login countries), payment_file |
| Free text, templated stories | sending_bank_fraud_report (victim, amount, date, account ending, scam story), gift_letter (donor, DOB, amount, relationship), reverification_record |
| Free text, LLM-paraphrased | support_chat, staff_note, customer_email, customer_letter, email, venue_email, insurer_letter, solicitor_letter |

Free-text variety: bank `support_chat` has 123 distinct texts across 291 documents, e-money 96 across 170, investment 12 across 200. Most chats are a few stories told in different words, e.g. "sending money to help my family", "gift from my uncle", "loan from a friend", "my cousins invest through my account".

### 5.4 Example warning-sign documents

- **sending_bank_fraud_report**: another bank reports that its customer was scammed (APP fraud) and paid into this customer's account; it asks for a freeze. A strong ESCALATE signal (mule activity).
- **device_export**: a device used from the UK *and* Nigeria, or shared by several customers. Account misuse.
- **staff_note** (bank TM03): the customer asked whether keeping deposits under £10,000 avoids reporting, then said they would use another branch. Structuring.
- **staff_note** (Payments/FX TM03): the director asked whether splitting payments under £10,000 avoids the invoice request. Structuring.
- **staff_note** (Payments/FX TM06): the director would not explain why payments for Turkish goods go to Lebanon. High-risk corridor.

## 6. Earlier reviews and internal list

- `previous_reviews` verdicts are **always CLOSE**. Rules covered: bank TM01/04/03/05/06, Payments/FX TM01/04/06, e-money TM01/04/06, investment TM01/04. Investment has 109 earlier TM04 reviews even though TM04 doesn't run there, which shows that earlier reviews don't map to current activity. The policy agrees: they cover only what they describe.
- Internal list reasons are dominated by a few templates: "unresolved specialist investigation… companies they control", "exited after unexplained third-party payments", "used to receive scam proceeds", "named in a production order", "ran X Imports Ltd from Unit 4B". **Investment:** 152 of 169 entries are "exited after refusing to explain large cash deposits".
- TM22 `matched_on` values:

  | Institution | name | officer name + month/year of birth | registered address |
  |---|---|---|---|
  | Bank | 36 | 35 | ~38 (one per address) |
  | Payments/FX | 32 | 42 | ~22 |
  | E-money | 41 | 42 | ~45 |
  | Investment | 131 | – | – |

## 7. Case signatures (scenario templates)

Signature = sorted rule IDs in the case + sorted non-filler document types on the customer. `-` = no non-filler documents.

### 7.1 Bank (32 signatures)

| Cases | Rules | Documents |
|---|---|---|
| 75 | TM01+TM05 | support_chat |
| 39 | TM01+TM05 | insurance_schedule, vehicle_sale_receipt |
| 39 | TM01 | solicitor_letter |
| 39 | TM03 | cash_collection_reports, cash_handling_procedure, company_register_extract |
| 39 | TM02+TM04 | booking_confirmation |
| 38 | TM03 | cash_reconciliation, company_register_extract, ticket_report |
| 38 | TM01+TM02 | dealer_invoice, loan_agreement |
| 38 | TM22 | company_register_extract, receipt |
| 38 | TM06 | hospital_invoice, support_chat |
| 37 | TM01+TM07 | insurer_letter |
| 36 | TM22 | - |
| 36 | TM01+TM02+TM04 | sending_bank_fraud_report ×2, support_chat |
| 36 | TM02+TM04 | - |
| 36 | TM01+TM06 | support_chat |
| 36 | TM01+TM02 | - |
| 36 | TM01+TM02 | company_register_extract, support_chat |
| 36 | TM01+TM03 | company_register_extract, management_accounts, staff_note |
| 36 | TM01 | - |
| 35 | TM01+TM06 | venue_email |
| 35 | TM01 | bank_statement |
| 35 | TM01+TM02+TM05 | support_chat ×2 |
| 35 | TM22 | company_register_extract, invoice, significant_control_register |
| 32 | TM01+TM02 | company_register_extract ×3, consulting_contract |
| 25 | TM01+TM07 | bank_statement, reverification_record |
| 25 | TM01+TM07 | - |
| 20 | TM01+TM02 | bank_statement ×2 |
| 18 | TM02 | bank_statement ×2 |
| 13 | TM07 | bank_statement, reverification_record |
| 10 | TM07 | - |
| 4 | TM02 | company_register_extract ×3, consulting_contract |
| 3 | TM06 | venue_email |
| 2 | TM07 | insurer_letter |

### 7.2 Payments/FX (31 signatures)

| Cases | Rules | Documents |
|---|---|---|
| 210 | TM01 | bank_statement, company_register_extract |
| 42 | TM22 | company_register_extract ×2, invoice, significant_control_register |
| 42 | TM03 | company_register_extract, invoice, staff_note |
| 42 | TM06 | company_register_extract, staff_note, supplier_invoices |
| 42 | TM01 | company_register_extract, supplier_invoices |
| 41 | TM02+TM08 | company_register_extract ×3, consulting_contract |
| 41 | TM02+TM08 | company_register_extract |
| 34 | TM06 | company_register_extract, supplier_invoice, supply_contract |
| 34 | TM03 | company_register_extract, contract_schedule |
| 33 | TM01 | company_register_extract, purchase_order, supplier_invoice |
| 32 | TM22 | company_register_extract |
| 31 | TM01 | company_register_extract |
| 31 | TM03 | company_register_extract |
| 31 | TM04 | company_register_extract |
| 31 | TM07 | company_register_extract |
| 31 | TM06+TM08 | company_register_extract, credit_note |
| 30 | TM04+TM08 | company_register_extract, payment_file |
| 22 | TM22 | company_register_extract ×2, receipt |
| 22 | TM02+TM08 | company_register_extract, email, group_structure, supplier_invoice |
| 22 | TM07+TM08 | company_register_extract, insurer_letter |
| 22 | TM02+TM08 | carrier_invoice, company_register_extract, freight_invoices |
| 22 | TM07 | company_register_extract, reverification_record, supplier_invoice |
| 22 | TM04 | company_register_extract, payment_file |
| 21 | TM08 | company_register_extract |
| 19 | TM08 | company_register_extract, group_structure |
| 18 | TM08 | company_register_extract, factoring_agreement, remittance_advice |
| 14 | TM02+TM06+TM08 | company_register_extract, credit_note |
| 11 | TM02+TM04+TM08 | company_register_extract, payment_file |
| 3 | TM02+TM08 | company_register_extract, factoring_agreement, remittance_advice |
| 3 | TM02+TM08 | company_register_extract, group_structure |
| 1 | TM01+TM08 | company_register_extract, factoring_agreement, remittance_advice |

Note: every Payments/FX customer has a `company_register_extract` for itself, so in this institution only the *extra* register extracts (for counterparties) are signal.

### 7.3 E-money (30 signatures)

| Cases | Rules | Documents |
|---|---|---|
| 46 | TM02 | bank_statement ×2 |
| 46 | TM03 | takings_book |
| 46 | TM05 | advert, sale_receipt |
| 45 | TM01 | settlement_letter |
| 45 | TM06 | hospital_invoice, support_chat |
| 45 | TM02+TM04 | booking_confirmation |
| 45 | TM22 | company_register_extract, receipt |
| 45 | TM02 | deposit_return_letter, holding_deposit_receipt |
| 44 | TM01+TM07 | insurer_letter |
| 42 | TM01+TM02+TM04 | device_export, sending_bank_fraud_report ×2, support_chat |
| 42 | TM02 | - |
| 42 | TM01 | - |
| 42 | TM01 | bank_statement |
| 42 | TM02+TM04 | - |
| 42 | TM22 | company_register_extract, invoice, significant_control_register |
| 41 | TM01+TM02+TM03 | staff_note |
| 41 | TM01+TM02 | company_register_extract, support_chat |
| 41 | TM22 | - |
| 36 | TM01+TM06 | support_chat |
| 34 | TM01+TM07 | bank_statement, reverification_record |
| 33 | TM01+TM02 | device_export |
| 31 | TM01+TM07 | - |
| 26 | TM06 | venue_email |
| 19 | TM01+TM06 | venue_email |
| 12 | TM07 | bank_statement, reverification_record |
| 10 | TM07 | - |
| 9 | TM02 | device_export |
| 3 | TM01+TM04+TM06 | support_chat |
| 3 | TM01+TM02+TM06 | support_chat |
| 2 | TM07 | insurer_letter |

### 7.4 Investment (35 signatures)

| Cases | Rules | Documents |
|---|---|---|
| 71 | TM01+TM08+TM22 | gift_letter |
| 55 | TM01+TM08 | - |
| 50 | TM01+TM06+TM08 | support_chat |
| 50 | TM01+TM02+TM08 | company_register_extract, support_chat |
| 48 | TM01+TM02+TM08 | support_chat |
| 46 | TM01+TM08 | completion_statement |
| 46 | TM06 | bank_statement, customer_letter |
| 46 | TM10 | customer_email, redundancy_letter |
| 44 | TM01+TM02 | cancellation_confirmation |
| 38 | TM01+TM07 | bank_statement, reverification_record |
| 37 | TM01+TM06 | bank_statement, employment_contract |
| 36 | TM10 | - |
| 35 | TM01+TM08 | gift_letter |
| 35 | TM01 | bank_statement |
| 35 | TM08+TM10 | support_chat |
| 34 | TM01+TM02 | - |
| 30 | TM02 | bank_statement, customer_email |
| 29 | TM01+TM07 | - |
| 26 | TM01+TM08+TM22 | - |
| 25 | TM08 | bank_statement |
| 25 | TM07+TM08 | insurer_letter |
| 24 | TM08+TM22 | gift_letter |
| 21 | TM01+TM07+TM08 | insurer_letter |
| 20 | TM01+TM08 | bank_statement |
| 17 | TM08 | - |
| 15 | TM01+TM08+TM10 | support_chat |
| 15 | TM01+TM02 | bank_statement, customer_email |
| 10 | TM08 | gift_letter |
| 10 | TM08+TM22 | - |
| 8 | TM07 | bank_statement, reverification_record |
| 8 | TM06 | bank_statement, employment_contract |
| 7 | TM07 | - |
| 2 | TM02+TM08 | support_chat |
| 1 | TM02 | cancellation_confirmation |
| 1 | TM02 | - |

## 8. Worked examples (bank)

| Case | Alerts | Deciding evidence | Likely verdict |
|---|---|---|---|
| BK-CS100162 | TM01+TM05: £14,250 cash deposit | Chat CHT-4110 says it is the cash withdrawn in July for a car purchase that fell through. Withdrawal BK-T565241250 on 17 July is exactly £14,250; earlier review BK-R163138 closed that withdrawal | CLOSE |
| BK-CS100551 | TM01+TM05: £13,000 cash deposit | Vehicle sale receipt SAL-9402 for £13,000 dated the day before; insurance schedule INS-5321 shows the customer insured that car | CLOSE · DOCUMENTED_EVENT |
| BK-CS103088 | TM22 on name only: £1,315 to Noel Rayton | Nothing on file shows the payee's date of birth or address to compare with list entry BK-L21007 | RFI · RFI_COUNTERPARTY_DETAILS |
| BK-CS125943 | TM22 officer match: £1,800 to Elmcote Consulting Ltd | **Trap.** Invoice INV-5407 matches, and register REG-6142 shows the listed director resigned on 14 Sep, the day before the payment. But PSC register PSC-5155 shows they held 60% of the shares until 17 Sep, after the payment on 15 Sep | ESCALATE · ESC_LISTED_PARTY |

## 9. Within-signature consistency

Two bank signatures tested against every case in them:

| Signature | Check | Result |
|---|---|---|
| TM01+TM05 + vehicle_sale_receipt (39 cases) | Receipt amount = flagged cash deposit; days between receipt and deposit; number plate on receipt = insurance schedule | All 39: amount matches, 1 day apart, plate matches, so all closable |
| TM22 + significant_control_register (35 cases) | Listed person's director resignation date and PSC "Ceased" date vs payment date | All 35: resigned *before* the payment but ceased control *after*, so all are the listed-party trap |

So far each signature has one verdict, and the signature says which check decides it. We still need to run that check on every case, because the unseen set may include a variant that fails it.

## 10. Implications for the build

1. **Rule engine keyed on the scenario.** Find the scenario from the rules plus the document types, run its deciding check, and use the policy-based default when no scenario matches. This keeps every run deterministic, which covers the 20% reproducibility score.
2. **Parse documents with code.** Most deciding documents have fixed fields or tables. Match amounts to the penny, compare dates against transaction dates, and compare names and dates of birth against the list entry and the customer.
3. **LLM only for the paraphrased text.** Chats, staff notes, emails and letters need classifying: is this an explanation, a red flag or a contradiction? Use temperature 0 with a fixed set of labels, and cache by content hash inside `system/`. That is a few hundred short calls per run, well under the US$4 per 200 cases budget.
4. **Signatures with no documents** (e.g. bank `TM01 | -`, `TM22 | -`, investment `TM01+TM08 | -`) are the hardest calls. Choose between RFI and ESCALATE from the transaction pattern: is money moved on, and to whom?
5. **Hand-label one or two cases per signature** (about 128 labels) to build a scorer against the cost matrix.
6. **Next step:** a signature catalogue. For each signature, record a typical case, the deciding check, and the expected verdict with its reason code. Start with the bank.

## Appendix: analysis scripts

These scripts, all standard-library Python, were used to produce this report. They are in the session scratchpad, so copy them into the repo to keep them:

- `profile.py`: row counts, rule mix, join integrity, channels, countries, evidence types
- `docs.py`: evidence types with average length and fixed fields
- `signatures.py`: case signature counts
- `bundle.py`: prints a full case: `python3 bundle.py bank BK-CS100162` or `python3 bundle.py bank TM22 3`
- `variants.py`: the within-signature checks in section 9
