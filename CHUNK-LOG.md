# Repaydesk — Chunk Log

Build journal for Repaydesk. One `## Chunk N` section per chunk, in order. A chunk is a logical
unit of work that can be verified on its own, and lands as one commit on `main`.

**Rule: each section reflects the current truth of that chunk. When a later change corrects or
supersedes an earlier chunk, overwrite that chunk's section in place** — no separate amendments or
changelog.

Prose says what a thing does before naming it. Identifiers, file paths, configuration keys and
commands stay exact and in code formatting. **What & why** and **Principles** carry one real-world
metaphor per chunk.

Each entry uses the standing format:

- **What & why** — the change and its reasoning
- **Principles** — the engineering principle the change demonstrates
- **Files** — what the chunk adds or changes
- **Depends on / blocks** — ordering
- **Verification** — how it was proven, including what it does not prove
- **PR package** — the commit subject and description
- **Plan** — the milestone in the [project plan](https://claude.ai/code/artifact/c6b85112-30f7-4498-9c56-a9d8eb6b3063)
  this chunk advances

Status legend: ✅ done · 🚧 in progress · ⛔ blocked · ⏭️ deferred

---

## Chunk 1 — foundations and data ✅

_Date: 2026-10-08 · Tagged `m1`. Written up after the fact; the chunks that follow are logged as they
land._

**What & why.** _Metaphor: a flight simulator._ Pilots learn on a simulator because it behaves like
the aircraft — the same instruments, the same failures — without anyone's life on the line. Repaydesk
needs the same for a bank: a core banking system, a ledger and a week of incoming money that behave
like the real thing, so the hard work (integration, matching, posting) is real work rather than work
against mocks.

This chunk builds the simulator:

- A compose stack: Apache Fineract 1.12.1 as core banking, Odoo Community 18 as the ledger, Postgres
  with one database per system, the OpenTelemetry Collector and Jaeger. Self-hosted Langfuse is an
  optional overlay, `compose.langfuse.yaml`.
- Adapters behind contracts in `adapters/ports.py`: `CoreBanking` (Fineract), `Ledger` (Odoo),
  `StatementSource` (five file formats), `PaymentWebhooks` (signed provider deliveries) and
  `Messaging` (in memory for now). Reads and writes are separate protocols, because agents will get
  only the read side.
- A synthetic generator: 2,000 borrowers across nine branches, 33 lending groups, about 2,100 loans
  with schedules and repayment history, and a week of incoming payments written the way each source
  would deliver them. The hard cases are designed in at known rates, and what each line really is goes
  to `data/truth/truth.json`, apart from the lines.
- A seeder that loads all of it idempotently: a re-run skips what exists, and reuses the first run's
  seed and date so the book comes out identical.
- Repaydesk's own database, with an append-only audit log enforced by a trigger rather than by
  convention.

**Findings worth carrying forward.**

- *Fineract's name search is a case-sensitive substring match* and has nowhere to store a trading
  name. The adapter searches word by word in title case, and keeps trading names in a
  `client_business` datatable, which only supports exact lookups. Fuzzy search belongs in a borrower
  directory, milestone 3.
- *Group names must be unique in Fineract*, which a data-integrity error on the first full seed
  revealed. Names now carry the branch, and a cycle number when one repeats.
- *Two borrowers share the trading name "Ifeoma Pharmacy"*. That is a genuine ambiguity, kept on
  purpose: name alone cannot place their payments, so the investigator agent will have to use amount
  and branch.
- *On Windows, `localhost` adds two seconds to every call* by trying IPv6 first. Odoo contract tests
  took 200 seconds; with `127.0.0.1` they take 30. Every script and document uses `127.0.0.1`.
- *Fineract's image has no bash*, so the first health check never passed. It now calls the actuator
  endpoint with `wget`.
- *Langfuse v4 retired its traces and metrics read endpoints*; `api/public/v2/observations` is the one
  that answers.
- *"Today" is the bank's date, not the machine's.* Fineract runs on `Africa/Lagos` and dates arrears
  by it, while CI runners and containers run on UTC, so from 23:00 to midnight UTC the two disagree.
  A CI run at 23:22 UTC on 8 October failed the arrears contract test (34 days late against an
  expected 33). The seeder would have dated a book a day behind Fineract in the same hour. Everything
  now takes the date from `domain.bank_today()` (`BANK_TIMEZONE`, default `Africa/Lagos`), with
  `tzdata` as a dependency because Windows has no time zone database. _Corrected in chunk 4._
- *Odoo's generic chart already uses six of Repaydesk's account codes.* 110100 to 110500 are its
  stock accounts and 131000 is "Tax Paid", so the seeder found them and created nothing: the
  collection accounts and loan principal carried Odoo's names, and the collection accounts weren't
  of type cash. Balances were on the right codes; the names were wrong. Setup now renames and
  retypes an existing account to Repaydesk's, and an Odoo contract test checks every code.
  The contract tests' ledger setup also renamed the demo's company to "Contract Test Bank"; it now
  uses `BANK_NAME`. _Corrected in chunk 8, whose first postings showed it._

**Principles.** _Same simulator metaphor._ *Practise on the real instruments* — integration work
against mocks proves the mocks. *One contract, two implementations* — every contract test runs against
an in-memory reference and against the real system, so a bank's own adapter has a definition of
done. *Keep the answer key out of the exam room* — the truth file lives apart from the lines, and
nothing an agent can reach will read it. *Same seed, same book* — an eval that moves means the code
moved, not the data.

**Files.** Everything in the repository at `m1`: `compose.yaml`, `compose.langfuse.yaml`, `infra/`,
`services/core/` (package `repaydesk`: `domain.py`, `adapters/`, `synth/`, `db.py`, `telemetry.py`,
`seed.py`, `migrations/001_foundations.sql`, tests), `scripts/dev.py`, `Makefile`, `docs/adr/0001`
to `0004`, `.github/workflows/ci.yml`, `README.md`.

**Depends on / blocks.** Depends on nothing. Blocks every later chunk.

**Verification.** `ruff` clean. `pytest`: 23 passed, 7 deselected (the integration contracts).
`pytest -m integration` against the running stack: 7 passed — the same contracts the in-memory
adapters pass.

A clean `reset` then `up` ran end to end and the result was inspected rather than assumed:

- Fineract: 2,122 loans; spot-checked loans show schedules, outstanding balances and days past due.
- Odoo: company in NGN, five collection journals plus the repayment journal, and an opening entry of
  ₦1,140,981,890.00 principal outstanding.
- The week's files: 356 payment lines across the five sources.
- Jaeger: the seed is one trace with 6,287 spans.
- Langfuse: a test model-call span arrived as a generation, and a plain span sent alongside it was
  filtered out.

**Corrected after the first push.** CI's stack job seeded Fineract and Odoo on the runner, then
failed writing the week's files. On Linux, Docker creates a missing bind-mounted folder as root, and
the seeder runs as a non-root user. Windows Docker Desktop hides this. CI and `scripts/dev.py` now
create `data/` before compose starts (commit `ab8cfc0`). The lint, test and compose-validation jobs
passed on the first run.

**PR package.**

- Subject: `Milestone 1: foundations and data`
- Description:
  > Adds the local stack (Fineract, Odoo, Postgres, OpenTelemetry Collector, Jaeger, optional
  > Langfuse), adapters behind contracts with contract tests against both reference and real
  > systems, a synthetic loan book and a week of messy payments in five formats with the truth kept
  > apart, Repaydesk's database with an append-only audit log, an idempotent seeder that leaves one
  > trace, and CI.

**Plan.** Milestone 1, foundations and data.

---

## Chunk 2 — ingestion ✅

_Date: 2026-10-08_

**What & why.** _Metaphor: a post room with a ledger at the door._ Everything that arrives is logged
on entry. A letter delivered twice is still logged both times, but the second entry says "copy of
entry 41" and nobody acts on it again. A parcel that has been opened and resealed is refused at the
door and never reaches a desk. Matching, approval and posting all depend on the post room getting
this right: a payment counted twice is posted twice, and one dropped is a borrower wrongly in arrears.

Two ways in, one way to store:

- **Statement files.** `python -m repaydesk.ingest` reads new files from the four file sources
  (bank CSV, MT940, POS settlement, branch cash). A file already seen, meaning the same source, name
  and bytes, is skipped whole.
- **Webhooks.** `POST /webhooks/payrail` on the new API checks the provider's HMAC signature, stores
  the line and answers. It does the least it can at the door: matching happens later, so a slow
  match can never make the provider think a delivery failed.
- **One path to storage.** Both end in `store_line`. A line is known by its source and the source's
  own reference, such as `bank_csv:NGT…` or `virtual_account:evt_…`. The first sighting is the
  original. Any later sighting is stored too, with status `duplicate` and `duplicate_of` pointing at
  the original, and numbered `#2`, `#3` and so on.

**Decisions worth recording.**

*The database decides what is a duplicate.* A partial unique index allows one original per
reference (`payment_lines_one_original`). An insert that loses becomes a duplicate. Checking first
and then inserting would let two simultaneous deliveries of one event both become originals. A test
fires six at once and gets exactly one original and five duplicates, with no retries.

*Simultaneous sightings wait, they don't fail.* The original's insert has no conflict target, so a
clash on the line's id counts as well as a clash on the reference: the later delivery waits for the
earlier one, then becomes its duplicate. Duplicates are numbered `#2`, `#3` and so on; two that count
the same number take turns, and the one that loses tries the next free number. _Corrected in chunk 4:
the first version raised a unique violation about one run in eight under six simultaneous deliveries,
which the test's retry loop had hidden. A webhook has no retry loop, so the provider would have got a
500. A transaction-scoped lock per reference was tried and dropped. A statement file is one
transaction, and Postgres's shared lock table (64 per connection by default) would run out on a
file of a few thousand lines._

*Duplicates are kept, not dropped.* Dropping a redelivery would leave no trace that it happened. When
a provider later says it sent something twice, or a bank resends a statement, the record shows it
arrived and was recognised.

*A duplicate gets a 200.* The provider did nothing wrong. An error would make it retry, and the
retries would become more duplicates.

*A forged delivery gets a 401 that says nothing.* No echo of the body and no hint which part failed,
because either would help a forger. Every delivery is recorded in `webhook_deliveries` with a hash of
the body, but not the body itself, which carries the payer's name.

*The provider's folder is not read as files.* In the running system those payments arrive over HTTP.
`--replay-webhooks` delivers the recorded week to the API one request at a time, as the provider
would, and passes its trace context so the replay and the API's handling are one trace.

**A bug the tests found.** Files were first recognised by their hash alone. Every day with no POS
payments produces the same settlement file, a header and nothing else, byte for byte, so the second
empty day was skipped as "already ingested". Harmless for an empty file. The same rule would also skip
any two genuinely different files that happen to match. A file is now "seen before" only if the same
source sent the same name with the same bytes. A statement resent under a new name is read again, and
its lines are caught one by one as duplicates, which is the job of the line-level guard anyway.

**Principles.** _Same post-room metaphor._ *Put the guard where it can't be raced* — a unique index
holds under concurrency, and a check-then-insert does not. *Log the copy, don't bin it* — a
recognised duplicate is evidence that something arrived. *Do the least at the door* — the webhook
answers in milliseconds and leaves the thinking for later. *Fail closed* — an unsigned or forged
delivery stores nothing.

**Files.** New: `services/core/src/repaydesk/ingest.py`, `api.py`, `migrations/002_ingestion.sql`,
`tests/conftest.py`, `tests/test_ingest.py`. Changed: `pyproject.toml` (FastAPI, uvicorn, psycopg-pool,
FastAPI tracing), `compose.yaml` (the `api` service on port 18000 and the `ingest` tool),
`scripts/dev.py` (`ingest` task, database tests in `test`), `Makefile`, `.github/workflows/ci.yml`
(Postgres for the database tests; ingestion and replay in the stack job), `README.md`.

**Depends on / blocks.** Depends on chunk 1. Blocks chunk 3, which matches the lines stored here.

**Verification.** `ruff` clean. `pytest`: 32 passed, 7 deselected. The 9 new database tests run
against a real Postgres, each in its own throwaway schema, so they meet the real index, constraint
and trigger:

- the week's files ingest once, and a second run skips every file;
- every stored line's id equals its truth key;
- a resent statement under a new name produces only duplicates, each `#2` of its original;
- six simultaneous deliveries of one event produce one original and five duplicates, without retrying
  (40 runs in a row since the chunk 4 correction);
- every file leaves an audit row, and `UPDATE` or `DELETE` on the audit log is refused;
- the webhook stores a delivery, then recognises its redelivery;
- a forged delivery gets a 401, stores nothing, and is recorded as rejected;
- an event that isn't a credit is ignored;
- replaying the week's webhooks gives exactly the truth file's originals and duplicates.

On the seeded stack, `python scripts/dev.py ingest` stored 55 files with 210 lines, plus 141
webhook lines and 5 redeliveries marked duplicate. That reconciles with the truth file: all 356 lines
present, and every `duplicate_delivery` line marked duplicate. A second run stored nothing. Jaeger
shows a replay as one trace spanning `repaydesk-ingest` and `repaydesk-api`.

**PR package.**

- Subject: `Chunk 2: ingestion of statement files and provider webhooks`
- Description:
  > Adds ingestion. Statement files are read from the inbox, skipping any file already seen.
  > Provider webhooks arrive at `POST /webhooks/payrail` on a new API, signature checked. Both store
  > through one function. The first sighting of a source reference is the original; later sightings
  > are kept as duplicates pointing at it, decided by a partial unique index so simultaneous
  > deliveries can't both win. A forged delivery gets a 401 and stores nothing. Every delivery is
  > recorded, with the body's hash rather than the body. The replay tool delivers the recorded week
  > as the provider would and passes trace context through.
  >
  > Fixes, found by the tests: files were recognised by hash alone, so identical empty POS files
  > on different days were skipped.
  >
  > 32 tests passing, including 9 against a real Postgres. Reconciled against the truth file on
  > the seeded stack: 356 of 356 lines, and every redelivery marked duplicate.

**Plan.** Milestone 2, rules, approvals and gateway: ingestion for files and webhooks.

---

## Chunk 3 — rule matching ✅

_Date: 2026-10-08_

**What & why.** _Metaphor: a sorting office that only sorts what is clearly addressed._ A letter
with a full, legible address goes straight into the right pigeonhole. A letter with half an
address, a smudged postcode or only a nickname goes to the dead-letter desk, where someone
experienced works it out. The sorter is never allowed to guess, because a guessed letter lands in
the wrong house and nobody notices until the real recipient complains. In Repaydesk the wrong house
is someone else's loan, and the complaint comes from a borrower being chased for a payment they
made.

This chunk adds the sorter. Rules propose only what is certain, and the rest waits for the
investigator agent in milestone 3:

- `virtual_account` — the money arrived in a loan's own virtual account.
- `loan_reference` — the line quotes a loan reference that exists and is active, the payer's name
  includes the borrower's surname, and one more independent thing agrees: an amount the loan has
  due, or the same branch. A line from a known branch other than the borrower's is never matched by
  rule. A POS line has no name at all, so there the branch and an amount due must both agree.
- `pos_phone` — the agent typed the borrower's phone, and that borrower has exactly one active loan.
- `not_repayment` — the narration is a known kind of credit that isn't a repayment: a reversal, the
  bank's own interest, an internal float top-up. Proposed for exclusion, still approved by a person.

A proposal is a row in the new `proposals` table: what the line is (`match` or `exclude`), which
loans with how much, who proposed it, and the evidence checked, kept for the reviewer. The database
allows one live proposal per line. A rejected one stays as history.

`repaydesk.scoring` compares proposals with the truth file case by case. It is the yardstick for the
agent too, and CI now runs it after matching and fails the build on any wrong proposal.

**How the reference rule got its shape.** Each version of the rule was swept across many generated
books, and each sweep found a real way to be wrong:

1. *Reference plus a matching amount.* On the seeded book, a cash slip quoting a mistyped
   reference was matched to another borrower's loan. Loan numbers are dense, so the typo named a
   real loan. Instalments are standard sizes, so that loan had ₦363,000 due, exactly the amount paid.
2. *Reference plus any shared name.* Across 40 books, FRIDAY IKPE's mistyped reference named Friday
   Briggs's loan, which had the amount due. A shared first name confirmed it. The payer's surname
   contradicted it.
3. *Reference plus the surname and a second shared name.* Across 100 books, two different
   borrowers both called Yetunde Salami, at Oshodi and Ibadan, met through a mistyped cash slip.
   The slip came from the other branch.
4. *The rule as it stands* — surname, plus an amount due or the same branch, with another branch as
   a veto. Across 100 books and 35,053 lines: no wrong proposal.

Each found case is now a named test, and 20 of the 100 books run in the suite on every push.

**The price, stated plainly.** Rules propose about 60% of lines. Of quoted references, 71% of
transfers and 82% of cash slips are matched by rule; the rest have a truncated surname, a relative
paying, or a partial amount. They go to the agent, which can weigh evidence a rule can't. A rule
that matched 5% more lines and was wrong once in a few thousand would be a worse system.

**Principles.** _Same sorting-office metaphor._ *Certain or not at all* — a rule's errors are
invisible until a customer complains, so rules carry no confidence below 1. *Two independent
witnesses* — a reference is a claim and a name or an amount alone is a coincidence, so it takes
agreement between things that fail differently. *Let a contradiction veto* — a payment at another
branch is evidence against, not just absence of evidence for. *Measure across many worlds, not
one* — every flaw above was invisible in the single seeded book and plain across a hundred.

**Files.** New: `services/core/src/repaydesk/match.py`, `scoring.py`, `migrations/003_proposals.sql`,
`tests/test_match.py`. Changed: `adapters/ports.py`, `memory.py` and `fineract.py` (`get_borrower`
added to the read contract), `tests/contract/test_contracts.py`, `compose.yaml` (the `match` tool),
`scripts/dev.py` (`match` and `score` tasks), `Makefile`, `.github/workflows/ci.yml` (matching and the
scoring gate in the stack job), `README.md`.

**A test that failed for the wrong reason, twice.** The Fineract contract test for name search
failed about one run in five. Its synthetic surnames mixed letters and digits (`ContractF0Da1717`),
and Python's `title()` capitalises a letter after a digit, so a tag starting with a letter made a
case-sensitive miss. Real surnames have no digits, so the test now builds them from letters. The
new sweep test first fed redelivered webhooks to the matcher, which never happens in the real flow,
because ingestion marks them first. It now skips them, as the scorer does.

**Depends on / blocks.** Depends on chunk 2. Blocks chunk 4, which approves or rejects these
proposals.

**Verification.** `ruff` clean. `pytest`: 63 passed, 7 deselected. That covers the found-case tests,
coverage floors per case, a 20-book sweep asserting no wrong proposal, and a database test for the
one-live-proposal index, the match-needs-loans check, and the audit rows. The `get_borrower`
contract passes against Fineract, six runs in a row.

On the seeded stack, against real Fineract, `python scripts/dev.py match` then `score` gave 218
of 351 lines proposed, 218 correct, 0 wrong:

| Case | Lines | Proposed | Correct |
|---|---:|---:|---:|
| virtual_account | 141 | 141 | 141 |
| exact_ref | 42 | 26 | 26 |
| cash_ref | 24 | 20 | 20 |
| pos_phone | 17 | 17 | 17 |
| not_repayment | 8 | 8 | 8 |
| pos_ref | 7 | 6 | 6 |
| everything else | 112 | 0 | 0 |

What it does not prove: that the rules are never wrong on real statements. The sweep covers the
coincidences the generator can produce, and a real bank will have others. The pilot's shadow mode
(plan, stage 3) measures exactly that before any rule posts for real.

**PR package.**

- Subject: `Chunk 3: rule matching, and scoring against the truth`
- Description:
  > Adds rule matching that proposes only certain cases. Those are a loan's own virtual account,
  > a quoted reference confirmed by the borrower's surname and an amount due or the same branch, a
  > typed phone with one active loan, and known non-repayment credits. Everything else waits for
  > the agent. Proposals are stored with their evidence; the database allows one live proposal per
  > line.
  >
  > Adds scoring against the truth file, case by case, now a CI gate: any wrong proposal fails the
  > build.
  >
  > The reference rule was tightened three times by sweeping it over generated books, and each
  > found case is a named test. Final: 0 wrong across 100 books (35,053 lines), about 60%
  > coverage. On the seeded stack: 218 of 351 proposed, all correct.

**Plan.** Milestone 2, rules, approvals and gateway: rule matching.

---

## Chunk 3a — Fineract's staff screens, and where to look ✅

_Date: 2026-10-08 · Added between chunks 3 and 4 at the user's request._

**What & why.** _Metaphor: windows in the engine room._ A ship's engine runs fine whether or not
anyone can see it. But an inspector who can only read gauge printouts has to take the engineer's word
for everything; give them a window and they can watch the shaft turn. Fineract was running with no
window. Its image is the server alone, so a borrower, a schedule or a posted repayment could be seen
only through API calls. For a demo that is the difference between "trust me, it posted" and "here it
is on the loan".

This chunk adds the window: the **Mifos X web app** (`openmf/web-app:1.14`), Fineract's own
open-source staff screens, as a compose service on port 18081. It also records what every system in
the stack offers in a browser, because that question has an answer per system and it's worth not
working out twice.

**Where to look.** Every address below was checked on the running stack. Logins are the local demo
values from `.env.example`; change them in `.env`.

| System | What it is | Address | Login | Worth opening |
|---|---|---|---|---|
| Fineract, staff screens | Mifos X web app (added here) | http://127.0.0.1:18081 | `mifos` / `password`, tenant `default` | A borrower; their loan's **Repayment Schedule** (arrears in red); **Transactions** (external ids) |
| Fineract, API reference | Swagger UI, built in | http://127.0.0.1:18080/fineract-provider/swagger-ui/index.html | as above, on each call | Any endpoint the adapter uses |
| Odoo | The full Odoo web app, built in | http://127.0.0.1:18069 | `admin` / `admin` | **Invoicing → Accounting → Journal Entries** (the opening entry); **Chart of Accounts** |
| Jaeger | Trace search, built in | http://127.0.0.1:16686 | none | Service `repaydesk-ingest`, operation `ingest.replay_webhooks`: one trace across ingest and the API |
| Repaydesk | Its own web app (chunk 5) | http://127.0.0.1:18082 | `amaka`, `kunle`, `tunde` or `hadiza`; `STAFF_DEMO_PASSWORD` in `.env` | **Payments** → any line's case file; **Ingestion**; **Audit log** |
| Repaydesk API | FastAPI's docs, built in | http://127.0.0.1:18000/docs | staff sign-in from chunk 4: `POST /auth/sign-in`, then **Authorize** with the token | `GET /queue`; `POST /webhooks/payrail` |
| Langfuse (optional) | Model-call traces | http://127.0.0.1:3000 | `admin@repaydesk.local`, password in `.env` | Generations, from milestone 3 |

Useful direct links in the staff screens, which the app routes by id:
`/#/clients/<client id>/general` and `/#/clients/<client id>/loans-accounts/<loan id>/repayment-schedule`.
A loan's ids come from `GET /loans/external-id/LN-2026-001209`.

Odoo Community hides some accounting menus by default. If they're missing, turn on developer mode
under Settings, then tick "Show Full Accounting Features" on the admin user.

**Decisions worth recording.**

*The browser's address, not the compose one.* The web app is a single-page app: it runs in the
browser and calls Fineract from there. So `FINERACT_API_URL` is the address the browser can reach
(`http://127.0.0.1:18080`), not `http://fineract:8080`, which only exists inside compose. It is a
variable, `FINERACT_PUBLIC_URL`, because the hosted demo will put both behind one HTTPS address.

*No proxy needed.* Fineract already answers cross-origin requests from the app's address: a
preflight from `http://127.0.0.1:18081` returns the matching `Access-Control-Allow-Origin` and
credentials. So the app talks to Fineract directly, as it would in a bank's deployment.

*A pinned release.* `1.14` is the newest numbered release; the `dev` tags change daily.

*Always on.* It is an nginx serving static files, a few megabytes of memory, and it's the screen a
reviewer most wants. It has a health check, so `up --wait` waits for it too.

**What it showed straight away.** Zainab Shehu (`CUS-000950`, Abuja Wuse) has a six-month SME loan
of ₦300,000. The first two instalments are paid on their due dates, and the third, ₦60,500 due 6
October, is two days past due and shown as arrears. That is exactly the "steady payer with one
instalment just due" the generator wrote. The transactions tab lists each repayment's external id
(`HIST-0001789`, `HIST-0001790`). The posting gateway's idempotency keys will appear in that same
column, so any repayment Repaydesk posts can be traced from core banking back to its approval. The
`client_business` datatable that holds trading names appears as its own tab on the borrower.

**Principles.** _Same engine-room metaphor._ *Show, don't assert* — a reviewer who can open the loan
needs no screenshot of a log line. *Configure for where the code runs* — a browser app's API address
is the browser's, and getting that wrong looks like a broken app rather than a wrong setting.
*Write down the map once* — "is there a UI for this?" has a different answer per system, so the
answers live here.

**Files.** Changed: `compose.yaml` (the `mifos` service), `scripts/dev.py` (URL list), `README.md`
(addresses and logins for every UI).

**Depends on / blocks.** Depends on chunk 1 (Fineract). Blocks nothing; the end-to-end demo in chunk 9
uses it to show a posted repayment landing on the loan.

**Verification.** In the browser: signed in, opened a borrower, the loan's repayment schedule and its
transactions, as described above. No console errors on any page. The service reports healthy, and
`docker compose config` is valid.

What it does not prove: every screen of the app against Fineract 1.12.1. The app's release is newer
than the server, and only the screens above were exercised. A screen that calls an endpoint 1.12.1
lacks would fail there, not elsewhere.

**PR package.**

- Subject: `Chunk 3a: Fineract's staff screens (Mifos X web app)`
- Description:
  > Adds the Mifos X web app on port 18081, pointed at Fineract's browser-facing address, with a
  > health check. Fineract already allows the cross-origin calls, so no proxy is needed. Verified by
  > signing in and opening a borrower, a loan's schedule and its transactions. Documents every web
  > interface in the stack, with addresses and logins, in the README and the chunk log.

**Plan.** Supports milestone 2's demo (a posted repayment visible in core banking) and milestone 6's
hosted demo.

---

## Chunk 4 — approvals: roles, limits, two people, and the outbox ✅

_Date: 2026-10-09_

**What & why.** _Metaphor: a bank's cheque-signing mandate._ A company's mandate says who may sign
cheques and up to how much: any one director below a figure, two above it, and at least one of
them a director whose authority covers the sum. The cashier checks the signatures against the
mandate before paying, not after. Once a cheque is paid, its counterfoil goes in the book.

Repaydesk's proposals are those cheques. Until now a proposal sat in the database with nobody able
to act on it. This chunk adds the mandate and the cashier's check:

- **Staff with roles and limits.** Officers ₦500,000, supervisors ₦5,000,000, auditors read only. Four
  demo reviewers (`amaka`, `kunle`, `tunde`, `hadiza`) are created on the API's first start, sharing
  `STAFF_DEMO_PASSWORD` from `.env`.
- **Sign-in.** `POST /auth/sign-in` checks a scrypt hash and returns a signed token valid for eight
  hours. Every request reads the staff row again, so a deactivated account stops at once. It stands
  in for the bank's single sign-on.
- **The review queue.** `GET /queue` lists pending proposals, largest payment first. Each item carries
  the line, the proposal and its evidence, the approvals so far, how many are needed, and for the
  person asking: whether they may approve, whether theirs would settle it, and if not, why.
- **Decisions.** `POST /proposals/{id}/approve` and `/reject`. The rules are in
  [ADR 0005](docs/adr/0005-approval-rules.md):
  - one approval up to ₦500,000;
  - two different people above it, at least one with a limit covering the amount;
  - nobody decides twice, and only a pending proposal can be decided;
  - a rejection needs a reason.
- **The outbox.** An approved match is written to `posting_outbox` in the same transaction as the
  approval. The row carries the loans and amounts, who approved, and the idempotency key `RD-P<id>`
  that becomes Fineract's external id and Odoo's entry reference when the gateway posts it (chunk 8).
  An approved exclusion moves no money, so its line is just marked `excluded`.
- **A rejected line goes to the agent.** Rule matching now skips any line with a rejected proposal.
  A person has said the obvious answer is wrong, so proposing it again would be noise.

**Decisions worth recording.**

*One function decides, and the screen asks it too.* `approvals.judge` takes a person, an amount and
the approvals so far. It has no database, so the queue uses it to grey out a button and explain
why, and the approve call uses it to enforce the rule. The screen and the rule can't drift apart.

*The approval and the hand-off are one transaction.* If the outbox row were written separately,
a crash between the two would leave either an approval the gateway never sees, or a posting
nobody approved. Locking the proposal and its line (`FOR UPDATE`) makes two reviewers clicking at
once take turns. The second one is told the proposal is already approved.

*The first approver can be anyone allowed to decide.* An officer can vouch for a ₦2.5m line and a
supervisor completes it. Two officers can't settle what neither could approve alone. A supervisor's
first approval lets an officer give the second. What matters is that someone with authority for
the sum signed.

*Each decision keeps the decider's limit at the time.* If a limit changes later, history still
shows who was allowed to approve what.

*Exclusions follow the same limits.* Wrongly excluding a ₦2.5m reversal costs as much as wrongly
posting it, so an exclusion is judged by the line's amount like any other proposal.

**A race found on the way, in chunk 2.** Running the whole suite turned up chunk 2's
concurrent-delivery test failing about one run in eight. Six deliveries of one webhook at once could
clash on the line's id, or two could claim the same duplicate number. The test retried and hid it,
but the webhook endpoint doesn't retry, so the provider would have got a 500. Fixed in `store_line`
without locks, and the test's retry loop removed. The details are corrected in chunk 2's section.

**And a time zone bug, from chunk 1.** CI for this chunk ran at 23:22 UTC, already the next day in
Lagos, and a Fineract contract test failed by one day of arrears. Fixed in a follow-up commit; the
details are in chunk 1's findings.

**Principles.** _Same mandate metaphor._ *Check the mandate before paying, not after* — the rules run
inside the transaction that would act on them. *Two signatures, one with authority* — the second
pair of eyes needs a limit to back it, or it's a ritual. *The counterfoil is written with the
cheque* — approval and hand-off commit together or not at all. *Least privilege by role* — an auditor
can see every decision and make none.

**Files.** New: `services/core/src/repaydesk/approvals.py`, `staff.py`,
`migrations/004_approvals.sql`, `tests/test_approvals.py`, `docs/adr/0005-approval-rules.md`.
Follow-up: `domain.py` (`bank_today`), `seed.py`, `synth/generator.py`, `adapters/memory.py`,
`tests/contract/test_contracts.py`, `pyproject.toml` (`tzdata`).
Changed: `api.py` (sign-in, `/me`, `/queue`, `/proposals/{id}`, approve, reject), `match.py` (skips
rejected lines), `ingest.py` (the race fix), `tests/test_ingest.py`, `compose.yaml` and `.env.example`
(`STAFF_DEMO_PASSWORD`, `SESSION_SECRET`), `README.md`.

**Depends on / blocks.** Depends on chunk 3's proposals. Blocks chunk 5 (the web app signs in here
and shows the queue) and chunk 8 (the gateway drains the outbox).

**Verification.** `ruff` clean. `pytest`: 81 passed, 7 deselected. The 18 new tests cover:

- the rules as a pure function: the threshold exactly and a kobo over it, an officer vouching and a
  supervisor completing, two officers refused, a supervisor first letting an officer finish,
  nobody approving twice, auditors refused, and an amount above every limit waiting;
- tokens that expire, can't be forged and can't be signed with another secret; hashed passwords;
- against Postgres:
  - an approved match reaches the outbox with its key and audit rows;
  - a large payment needs two people;
  - an approved exclusion posts nothing;
  - a rejection needs a reason and is never re-proposed by rules;
  - the database refuses a reasonless rejection, a second decision by one person, and an auditor
    with a limit;
  - two people approving at once give one posting;
- over HTTP: no token, a bad token, a wrong password and an unknown user all get 401. A scripted
  morning in the queue covers ordering, read-only auditors, the two-person flow, a reasonless
  rejection (422), approving a rejected proposal (409), and the proposal's full history.

Both concurrency tests passed 40 runs in a row.

On the seeded stack, against the rebuilt API:

- a request without a token got a 401;
- the queue listed the 218 rule proposals (200 per page), 23 of them needing two people;
- Hadiza was refused as read only;
- Amaka approved a ₦375,500 match, which went to the outbox as `RD-P110`;
- on the largest line, a ₦1,821,833 reversal proposed for exclusion, Amaka gave the first approval.
  Kunle was refused (`over_limit`). Tunde's approval settled it, and the line was marked excluded
  with no outbox row;
- Tunde rejected a virtual-account match for "Wrong borrower". Running matching again offered that
  line to no rule (133 left for the agent, as before);
- scoring: 217 of 351 lines proposed, 0 wrong.

What it does not prove: that anything posts. The outbox fills, and nothing drains it until the
gateway in chunk 8. Sign-in has no lockout after failed attempts. That is acceptable for throwaway
demo logins; a bank would use its single sign-on (plan, stage 4).

**PR package.**

- Subject: `Chunk 4: approvals with roles, limits and two-person approval, and the posting outbox`
- Description:
  > Adds staff sign-in and the review queue API. Officers approve up to ₦500,000, supervisors up to
  > ₦5,000,000, and auditors only read. Above ₦500,000 two different people approve, and one of
  > them must have a limit that covers the amount. Rejections need a reason, and rules never
  > re-propose a rejected line. The rules live in one pure function used both to show the queue
  > and to enforce decisions.
  >
  > An approved match is written to a posting outbox in the same transaction as the approval,
  > with the idempotency key the gateway will post under (chunk 8). An approved exclusion just
  > marks its line.
  >
  > Also fixes a race in chunk 2's ingestion: simultaneous deliveries of one webhook could fail
  > with a unique violation instead of being recorded as duplicates.

**Plan.** Milestone 2, rules, approvals and gateway: the approval queue with role limits.

---

## Chunk 5 — the web app: sign-in, Payments, Ingestion, Audit ✅

_Date: 2026-10-09_

**What & why.** _Metaphor: a case-file room._ In a records office, each matter has a folder.
Open it and everything is there in order: the original letter, the notes on it, who signed what
and when, and where it went next. Nobody has to ask three departments to piece the story
together. Until now, Repaydesk's story for a payment was spread across four tables and the audit
log, readable only with SQL.

This chunk opens the room. `services/web` is a React and TypeScript app, built to the
[approved mockup](https://claude.ai/artifact/3zvA1ZuM8f1CHQmRi89QiV) and with its styles:

- **Sign-in** as one of the demo reviewers. The signed-in person's role and limit show in the top
  bar. When the API ends a session (expired, or the account deactivated), the app returns to
  sign-in and says why.
- **Payments.** Every line, newest first, 50 a page. Filters for status (with counts) and channel,
  and a search across the payer, narration, line id and the loan a line was matched to. Filters
  live in the address, so a filtered view can be bookmarked or sent.
- **A payment's case file.** What arrived and how (the file it was in, or the webhook), and any
  later copies. The live proposal, with its loans and the rule's evidence turned into sentences
  marked ✓ or ✕. Who approved, the posting key waiting in the outbox, earlier rejected proposals
  with their reasons, and the trail from the audit log, each step linked to its trace in Jaeger.
- **Ingestion.** Lines per source, every statement file with its lines reconciled (stored plus
  duplicates equals the file's lines), and the latest webhook deliveries with signature and outcome.
- **Audit log.** Newest first, filtered by action, person or subject, with each row's detail and
  trace. It pages by id, so rows arriving meanwhile don't shift the page.
- **Screens still to come** — Today, Review queue, Borrowers, Loans, Agent runs, Collections and
  Measures — are in the navigation, labelled with the chunk or milestone that brings them.

The API gains the reads behind these (`views.py`): `GET /lines`, `/lines/{id}`, `/ingestion`,
`/audit`, `/summary`, and `/config` (the bank's name and the other systems' addresses, no secrets).

In compose, the app is the `web` service on port 18082: a Node build stage, then nginx serving the
files and passing `/api` to the API.

**Decisions worth recording.**

*One origin, no CORS.* The app calls `/api/...` on its own address. nginx in the container and
Vite in development pass it to the API, so there are no cross-origin requests and nothing to
allow. The content security policy can then say `connect-src 'self'`.

*The API strips `/api`, not the proxy.* Line ids contain `#` and `:` (`virtual_account:evt_…#2`).
When nginx rewrites a path, it decodes and re-encodes it first, which can mangle those. So
`proxy_pass` carries no path and the request goes through byte for byte; a small ASGI middleware
in the API drops the prefix. A test fetches a `#` id through `/api` with the browser's encoding.

*The proxy looks the API up as requests come in.* _Corrected after chunk 7._ As first written,
nginx looked up the `api` hostname once, when it started. Chunk 7 recreated the API container,
which got a new address, and every `/api` request from the app was refused (a 502) until `web`
restarted. nginx now asks Docker's DNS (`127.0.0.11`), with answers kept 10 seconds, and
`proxy_pass` names the API through a variable so the lookup happens per request. The path still
goes through untouched. Checked by recreating the API at a new address with `web` left running.

*"All" means money, not deliveries.* The first look at the running app showed 648 lines, 297 of
them duplicates. They are correct records: the webhook week was replayed three times while chunk
2 was verified, and every redelivery was kept. But a provider retrying a webhook ten times is not
ten payments. The default list and the navigation count now leave copies out. They have their own
filter, and every original lists its copies.

*Staff by name.* The audit log stores a login (`amaka`); the screens show "Amaka Nwosu", joined
from the staff table. Rules and agents read as "Rule: virtual account".

*Times in the bank's zone.* Moments show in Lagos time, the same as `bank_today()`. Value dates
are calendar days and never shift with the viewer's zone. A test pins 23:30 UTC on 8 October to
00:30 on the 9th.

*Pinned to the majors I know.* The newest releases on npm are React Router 8, TypeScript 7,
Vite 8 and Vitest 5. The app pins React Router 7, TypeScript 5.9, Vite 7 and Vitest 3, all current
within their majors, rather than guess at changed APIs. Upgrading is a chunk of its own once the
UI is complete.

*No install scripts.* npm 11 holds back package install scripts until approved. esbuild's is only
a check, and the build works without it, so nothing was approved.

**Principles.** _Same case-file metaphor._ *The whole story in one folder* — a reviewer should
never need SQL to learn what happened to a payment. *The record speaks in sentences* — evidence
recorded as keys is shown as the checks a person would make. *Copies are filed, not counted* —
duplicates stay visible, but never in a total of money. *Don't trust the courier with the address*
— a proxy that rewrites paths can change ids, so it carries them untouched.

**Files.** New: `services/web/` (the app, its tests, `Dockerfile`, `nginx.conf`),
`services/core/src/repaydesk/views.py`, `services/core/tests/test_views.py`. Changed: `api.py` (read
endpoints, `/config`, the `/api` prefix), `compose.yaml` (the `web` service), `scripts/dev.py` and
`Makefile` (`web`, `webtest`; `lint` type-checks the app), `.github/workflows/ci.yml` (a `web` job;
the stack job signs in through the app's origin), `README.md`.

**Depends on / blocks.** Depends on chunk 4 (sign-in, decisions, outbox). Blocks chunk 6 (the review
queue screen) and chunk 7 (borrowers and loans).

**Verification.**

- **Python:** `ruff` clean. `pytest`: 88 passed, 7 deselected. The 7 new tests run against a real
  ingested week with rules run and one approval. They cover:
  - every read refusing a request without a token;
  - paging that returns every line exactly once, and filters and counts that agree;
  - search by the matched loan;
  - a line's full story, from proposal to posting key;
  - a copy reached through `/api` by its encoded `#` id;
  - every file reconciling;
  - the audit log paging by id and filtering by action, actor and pattern.
- **Web:** `tsc` clean; `vitest`: 11 passed. They cover:
  - naira formatting, exact for large amounts;
  - dates in the bank's zone;
  - evidence sentences, including keys the app doesn't know;
  - against a stubbed API: a wrong password refused, sign-in landing on Payments with the token
    on every request, a `#` id requested encoded, and an ended session returning to sign-in.
- **The image:** builds, and is served healthy with `X-Frame-Options` and a content security
  policy. A deep link (`/payments/x`) gets the app.

In the browser, against the running stack, signed in as Amaka:

- Payments listed 351 lines, the same as the truth file, with 214 awaiting review.
- The case file for `RD-P110` showed the webhook arrival and its two copies, the rule's two checks,
  Amaka's approval, the pending posting and a four-step trail.
- The line Tunde rejected showed the rejected proposal with his reason, and "waits for the agent".
- Ingestion reconciled: 49 + 58 + 77 + 26 file lines plus 141 webhook lines is 351.
- The audit log filtered to the one rejection.
- No console errors.

What it does not prove: the layout at phone width. The responsive rules are the mockup's,
unchanged, but the browser window couldn't be resized during this session, and screenshots
failed on and off, so the pages were checked mostly by their text. Nor does it prove behaviour
under real load: paging is in place, but the biggest list tried has 648 rows.

**PR package.**

- Subject: `Chunk 5: web app with sign-in, Payments, Ingestion and Audit`
- Description:
  > Adds Repaydesk's web app (React, TypeScript, Vite), built to the approved mockup and served by
  > nginx on port 18082, which passes /api to the API on the same origin. Staff sign in, then
  > browse every payment line, filtered, searched and paged, and open any line's case file: what
  > arrived, the proposal and its evidence in plain words, who decided, the posting key, and the
  > audit trail with trace links. Ingestion shows every file and webhook delivery reconciled; the
  > audit log pages by id with filters.
  >
  > Adds the read endpoints behind it. Default lists leave out duplicate deliveries, which keep
  > their own filter. The API strips the /api prefix itself so ids with '#' pass through the proxy
  > intact. CI gains a web job (type check, tests, build) and a stack step that signs in through
  > the app's origin.

**Plan.** Milestone 2, rules, approvals and gateway: the staff UI, first screens.

---

## Chunk 6 — the review queue screen ✅

_Date: 2026-10-09_

**What & why.** _Metaphor: a signing desk with the mandate pinned above it._ Chunk 4 wrote the
mandate: who may sign for how much, and when two signatures are needed. A mandate that lives only
in the cashier's head gets discovered one bounced cheque at a time. Pin it above the desk and each
signer knows before picking up the pen whether this one is theirs to sign, and if not, whose.

This chunk is that desk: the **Review queue** screen, the mockup's two-column layout on the
chunk 4 API.

- **Each proposal is a card.** The payment on the left: channel, date, amount, narration, payer,
  any typed reference, and a link to the full line. The proposal on the right: who proposed it and
  how sure, the loans and amounts (or "Exclude: not a repayment"), and the checks in plain words.
- **Largest first, 25 a page, three views with counts:** all, needs two people, and not a
  repayment. The view lives in the address.
- **The buttons say what you may do.** "Approve", "Approve (first of two)" or "Give second
  approval". When you may not, the button is disabled and the reason sits beside it: above your
  limit and the first approver's, or you approved this already. Those reasons come from the API's
  `judge`, the same function that enforces the rule, so the screen and the rule can't disagree.
  An auditor sees every card marked read only, with no buttons.
- **Rejecting takes a reason:** one of four common ones, or "Something else" with free text. The
  reason goes to whoever looks next.
- **After a decision**, a short message says what happened ("Approved ₦968,000. It's queued for
  posting…"). The queue and the navigation counts refresh. If someone else decided first, the
  reviewer is told and the card goes.
- A payment awaiting review links to the queue from its case file.

The API's `GET /queue` now pages (`limit`, `offset`), takes a `view`, and returns each view's
count.

**Decisions worth recording.**

*Disable the button, and say why.* A greyed button with no reason trains people to ask a
colleague or to retry. The refusal text is the API's own, so it's always the real reason.

*The first approver can't reject.* The API already refused it. Found in the browser: Kunle's card
still offered Reject after his first approval, so a click would only have produced an error. The
button is now disabled with the reason.

*Show the meaning, not the regular expression.* Found in the browser: an exclusion's evidence
read "The narration matches the pattern \bHQ OPERATIONS FLOAT\b". A reviewer needs "Not a
repayment: an internal float top-up", which was already the line above it. The pattern stays in
the stored evidence for engineers, and the screen says "Recognised from the narration".

*Views on the server, not in the browser.* Filtering a page of 25 in the browser would hide
matches on other pages. The API filters and counts across the whole queue.

*"Edit split" from the mockup is left out.* Changing a proposal's allocation is a new kind of
decision, with its own audit and limits. It belongs with the agent's group splits in milestone 3,
when proposals that need editing first appear.

**Principles.** _Same signing-desk metaphor._ *Pin the mandate where the pen is* — each person
sees, before acting, whether a decision is theirs. *One rule, two readers* — the screen asks the
same function the API enforces. *A refusal is a sentence, not a grey box.* *Look at the real
thing* — two of this chunk's fixes were invisible in the tests and obvious in the browser.

**Files.** New: `services/web/src/pages/Review.tsx`, `Review.test.tsx`, `components/Checks.tsx`.
Changed: `approvals.py` (queue views, paging and counts), `api.py` (`/queue` parameters),
`tests/test_approvals.py`, `web/src/api.ts`, `components/common.tsx` (toasts, change events),
`components/Shell.tsx` (counts refresh after a decision), `App.tsx`, `pages/LineDetail.tsx` (link to
the queue), `pages/Later.tsx`, `evidence.ts`, `evidence.test.ts`, `README.md`.

**Depends on / blocks.** Depends on chunks 4 and 5. Blocks chunk 9's end-to-end demo, which runs a
payment through this screen.

**Verification.** `ruff` clean; `pytest`: 88 passed, 7 deselected. The queue test now also covers
each view's count, the views' contents, paging, and an unknown view refused. `tsc` clean; `vitest`:
19 passed. The 7 new tests on the queue screen, against a stubbed API that applies decisions:

- largest first, with loans and checks;
- an officer refused above every limit and told why;
- a first approval then waiting, with Approve and Reject both disabled for that reviewer;
- a small approval leaving the queue;
- a rejection with a written reason;
- someone else deciding first;
- an auditor with no buttons.

In the browser, against the running stack:

- Kunle (officer) opened "Needs two people" (22) and gave the first approval on a ₦968,000
  virtual-account payment. The card showed "1 of 2 approvals: Kunle Adebayo" and his buttons
  disabled with the reason.
- Signed in as Tunde (supervisor), the same card offered "Give second approval". Approving took it
  off the queue: all 214 → 213, two-person 22 → 21, and the navigation count with them.
- The reject form opened with its reasons and was cancelled.
- No console errors.

What it does not prove: the layout at phone width, as in chunk 5. Nor the queue at volume: it pages
on the server, but the largest queue tried has 214 proposals.

**PR package.**

- Subject: `Chunk 6: review queue screen`
- Description:
  > Adds the review queue: pending proposals largest first, each card showing the payment beside
  > the proposal and its checks. Approve, approve as first of two, or give the second approval.
  > Reject with a reason from a short list or written in. The buttons say what the signed-in
  > person may do, and why not, from the same rule the API enforces. Auditors see it read only.
  > After a decision the queue and navigation counts refresh; a proposal someone else decided
  > first is reported and removed.
  >
  > The queue API now pages, filters by view (all, needs two people, not a repayment) and returns
  > counts. An exclusion's evidence reads as its meaning rather than its regular expression.

**Plan.** Milestone 2, rules, approvals and gateway: the approval queue, on screen.

---

## Chunk 7 — Borrowers and Loans, on a borrower directory ✅

_Date: 2026-10-09_

**What & why.** _Metaphor: the branch's card index and the vault._ A teller looking for "the
Ifeoma with the pharmacy" flips through the card index by the door: quick, searchable, copied
from the ledgers each morning. But before paying anything out, they walk to the vault and read the
ledger itself. The index is for finding; the ledger is for deciding.

This chunk builds both halves.

- **The card index: a borrower directory.** Repaydesk's own copy of who borrows what: 2,024
  borrowers and 2,130 loans with their balances. A new `directory` service copies it from Fineract
  every 15 minutes, in about 20 seconds. The seed runs one sync at the end, so a fresh demo
  doesn't wait. Each sync writes only what changed and removes what Fineract no longer has, in
  one transaction. One sync runs at a time. A failed sync is recorded, shown on the screens, and
  leaves the last copy in place.
- **Borrowers.** Search by name, trading name, phone (in any format, `+234…` too), customer
  reference or loan. Filters for in arrears and payments not yet posted, with counts. Each row:
  branch, trading name, loans, arrears, and money Repaydesk holds for them.
- **Loans.** Most overdue first, with the mockup's bands (1 to 7 days late, 8 to 30, 31 to 90,
  over 90) and closed. Header: "2,130 loans · 646 in arrears".
- **The vault: each record's own page reads Fineract live.**
  - A loan: principal, outstanding, what's left **if held payments post**, its virtual account,
    the schedule (paid, part paid, late, "late · payment held"), and **money in, one timeline**:
    Fineract's repayments with their channel, then Repaydesk's held payments as hollow dots
    linking to their line.
  - A borrower: profile, loans with their state, and every payment from every channel.
  - Both link into Fineract's own staff screens.
  - If Fineract doesn't answer, the page says so and shows the copy with its time.
- **The search box** now opens a loan reference (`ln2026 001396` works), a customer reference or
  a phone directly. Anything else still searches payments.
- **Loan references are links everywhere:** Payments, the review queue and a line's case file.

**Decisions worth recording.** [ADR 0006](docs/adr/0006-borrower-directory.md).

*A copy for finding, live for deciding.* Fineract can't search the way staff do. Its name
search is case-sensitive, trading names only match exactly, and listing the book takes seconds and
many calls. Asking it on every keystroke would be slow and heavy on the bank's core system. But
a reviewer about to approve must see the loan now, not 15 minutes ago. So lists read the copy and
say how old it is, and a single record reads Fineract.

*Through the adapter contract, like everything else.* The directory reads through a new port,
`CoreBankingDirectory` (`list_borrowers`, `list_loans`). Fineract and the in-memory reference
adapter both implement it, and a contract test checks the listing agrees with single reads. A
bank's own core banking system plugs in the same way.

*Two Fineract quirks, absorbed in the adapter.*
- **Days late.** Fineract's loan list leaves `pastDueDays` at 0 for every loan; only a single
  loan's read fills it. The contract test caught it: 0 days late where the loan's own page said 3.
  The list's summary does carry the date the loan fell overdue, which gives the same number.
- **Trading names.** These sit in a datatable that can list which clients have a row but not the
  rows, so each name is its own call (1,108 of them, about 6 seconds). The adapter keeps them
  between syncs, reads only new ones, and rereads all once a day in case one was edited.

*Held payments are shown, never subtracted.* "Outstanding" is Fineract's figure. "If held
payments post" is shown beside it, so nobody mistakes an unapproved proposal for a posted
repayment, and a borrower who paid is never shown as unpaid. A rejected proposal holds nothing.

*Kobo stays integer through SQL.* Found in the first live check: Postgres returns a sum of
`bigint` as `numeric`, and the API sent arrears as the string `"6050000"`. Every sum is now cast
back to `bigint`, and a test checks the type.

**Principles.** _Same card-index metaphor._ *Find in the index, decide at the vault.* *Say how
old the copy is.* *Absorb the quirk where it lives* (the adapter), so the screens never learn it.
*Never let a pending payment pass for a posted one.*

**Files.**

- New:
  - `migrations/005_directory.sql`, `directory.py`, `records.py`;
  - `tests/test_records.py`;
  - `docs/adr/0006-borrower-directory.md`;
  - `web/src/pages/Borrowers.tsx`, `Loans.tsx`, `Records.test.tsx`, `components/records.tsx`.
- Changed:
  - `adapters/ports.py` (the directory port; a repayment's channel), `adapters/fineract.py`,
    `adapters/memory.py`, `tests/contract/test_contracts.py`;
  - `api.py` (`/borrowers`, `/loans`, `/find`; a read-only Fineract client), `seed.py`;
  - `compose.yaml` (the `directory` service; Fineract settings for the API), `scripts/dev.py`
    and `Makefile` (`directory` task), `.github/workflows/ci.yml` (smoke-checks a live loan read);
  - `web/src/api.ts`, `format.ts`, `format.test.ts`, `App.tsx`, `components/Shell.tsx` (search box),
    `pages/Payments.tsx`, `Review.tsx`, `LineDetail.tsx`, `Later.tsx`, `styles.css`;
  - `README.md`.

**Depends on / blocks.** Depends on chunks 1 (Fineract adapter), 3 (proposals) and 5 (web app).
Blocks chunk 8: the posting gateway's postings will appear on these pages as Fineract repayments
linked back to their lines (the `RD-P` reference link is already in place). Blocks milestone 3:
the agent's fuzzy borrower search runs over this directory.

**Verification.** `ruff` clean.

- `pytest`: 99 passed, 8 deselected. 10 new tests:
  - a sync copies the book, then only what changed (a repayment, a removed borrower and their loans);
  - a failed sync is recorded and keeps the last copy;
  - only one sync runs at a time;
  - search by surname, `+234` phone, trading name, loan and customer reference;
  - a borrower's page with live and held payments;
  - a page that falls back to the copy when core banking fails;
  - a loan's schedule and held amount (and nothing held after a rejection);
  - loan bands and their counts;
  - the search box's routing;
  - the endpoints over HTTP.
- Contract tests against the real stack: 16 passed. The new one checks Fineract's listing against
  its single reads, and a repayment's channel.
- `tsc` clean; `vitest`: 24 passed. The 4 new screen tests cover:
  - a loan ref in the search box opening the loan, with its schedule notes, "If held payments
    post" and the Fineract link;
  - a phone opening the borrower, and plain text still searching payments;
  - Fineract down;
  - loan bands and borrower search through the address.

In the browser, against the running stack:

- Loans: 2,130, most overdue first, band counts matching the API.
- LN-2026-001209 (Zainab Shehu): ₦242,000 outstanding live, ₦60,500 held, "If held payments
  post" ₦181,500, instalment 3 "late · payment held", and the timeline with branch cash and
  virtual account repayments then the held payment.
- Her borrower page: both loans (one closed), four payments across two loans.
- "ifeoma pharmacy" found the two Ifeoma Pharmacy borrowers at Onitsha and Ikeja, the mockup's
  namesake case.
- The search box opened a loan, a borrower by `+234` phone, and a borrower by customer reference,
  and sent `SHEHU` to payments.
- No console errors.

What it does not prove:

- **Fineract links.** They point at Mifos's routes, which are in its bundled app
  (`clients/:id/general`, `loans-accounts/:id/general`). I didn't click through: Mifos's sign-in
  didn't complete in the automated browser.
- **Typing in the browser.** Typed input didn't reach this tab and screenshots timed out, so the
  search box was driven by script. The API log confirms each search went where it should.
- **Phone width**, as in chunks 5 and 6.
- **Speed at scale.** A loan's page takes about 0.5 s and a borrower's about 2 s: they read
  Fineract live, one loan after another. Fine at this size; a bank with 50,000 borrowers would
  want the directory's sync and these reads measured.
- **Test borrowers.** The contract tests leave a few test borrowers (`CUS-T…`) in the local
  Fineract, so they appear in the local directory (2,024 rather than 2,000). The hosted demo won't
  run tests against its own Fineract.

**PR package.**

- Subject: `Chunk 7: Borrowers and Loans, on a borrower directory`
- Description:
  > Adds a borrower directory: Repaydesk's copy of the book, synced from Fineract every 15 minutes
  > by a new `directory` service through a new adapter port, for search and lists. Borrowers by
  > name, trading name, phone or loan; loans by how late. A borrower's or loan's own page reads
  > Fineract live (balances, schedule, repayments with their channel) and shows beside it the
  > payments Repaydesk holds but hasn't posted, with what the balance would be once they post. If
  > Fineract doesn't answer, the page says so and shows the copy. The search box opens a loan,
  > customer reference or phone directly, and loan references link to their loan everywhere.
  >
  > Fineract's loan list reports 0 days late for every loan; the adapter derives it from the
  > overdue date instead, caught by a new contract test. ADR 0006 records the copy-for-finding,
  > live-for-deciding split.

**Plan.** Milestone 2, rules, approvals and gateway: the records a reviewer checks before
deciding, on screen.

---

## Chunk 8 — the posting gateway ✅

_Date: 2026-10-09_

**What & why.** _Metaphor: the settlement clerk with the numbered stamp._ In a bank's back office,
an approved voucher goes to one clerk, the only person with the stamp for the ledgers. Before
stamping, the clerk checks the signatures against the mandate again; the voucher's word isn't
enough. Each voucher carries a number, so if the clerk is called away mid-entry, they look the
number up on return and finish the entry instead of making it twice. If the ledger room is locked,
the voucher waits in the tray; if the ledger refuses it, it goes back to a supervisor.

Until now, approved payments sat in the outbox with nobody to post them. This chunk adds the clerk:

- **The gateway** (`services/gateway`, .NET 10), the only writer to Fineract and the ledger
  (ADR 0002). Postgres wakes it the moment a match is approved (`NOTIFY posting_outbox`, raised by
  a trigger); a 15-second poll catches retries.
- **For each approved payment** it rechecks the approval against the decisions themselves, posts
  the repayment to Fineract under the idempotency key, posts a journal entry to Odoo that credits
  the loan exactly as Fineract split it (principal, interest, charges, overpayment), and marks the
  line posted, with its audit row, in one transaction.
- **Failures are sorted.** An outage (a timeout, a 5xx, a login problem) is retried from 10 seconds
  up to every 10 minutes, with the error and whatever Fineract already took on the row. A refusal (a
  closed loan, an approval the decisions don't support) stops and waits for a person.
- **Each service has its own Fineract login.** The seed creates `repaydesk-read`, read only, for the
  API, the directory and rule matching, and `repaydesk-gateway`, which may read a loan and post a
  repayment, nothing else. Until now everything used the admin login, so ADR 0002's "one writer"
  was a convention; Fineract now enforces it.
- **The case file** shows the posting: Fineract's repayment per loan with its split, the ledger
  entry's name, the attempts, the next attempt, or why it stopped.

**Decisions worth recording.** [ADR 0007](docs/adr/0007-posting-gateway.md).

*Hold the row while posting.* The gateway locks one outbox row (`FOR UPDATE SKIP LOCKED`) and
keeps the lock while it calls Fineract and Odoo, then writes the result in the same transaction.
There is no "claimed" state to expire: a crash drops the lock and the row is pending again
(migration 006 removes `claimed`). Two gateways each take a different row.

*Look before writing.* Each write is looked up by its key first: the repayment by Fineract's
transaction external id, the entry by its reference, and an entry left as a draft is posted. That
is what makes a retry safe, not matching error text. A split posts one repayment per loan,
`RD-P110-1`, `RD-P110-2`, because Fineract's external ids are unique per transaction; a single loan
keeps `RD-P110`, as the screens already show it.

*Don't trust the outbox row.* The gateway reapplies ADR 0005's rules to the proposal, the line and
the decision rows: the right kind and status, amounts that add up and match what was approved, the
people named being the people who decided, enough of them, one with the limit. A row written by
anything else is refused and never posted.

*Fineract first; the ledger follows its split.* Fineract decides how a repayment applies (interest
first, then any overpayment). The journal entry is built from Fineract's own answer, so the two
books can't disagree about it.

*Odoo shares one login.* Odoo Community has no fine-grained roles over JSON-RPC, so the seed and the
gateway share its admin login and nothing else has it.

**Found on the way.**

- *Odoo's generic chart already used six of our account codes*, so loan principal was named "Tax
  Paid" and the collection accounts carried stock account names. Balances were on the right codes,
  the names wrong. Fixed in the Odoo adapter's setup, with a contract test; corrected in chunk 1's
  section.
- *Fineract echoes a rejected password in its error.* Its password rule (12 to 50 characters, mixed
  case, a digit, a symbol, no character twice in a row) refused a generated one, and the error
  carried it. Setting up a login now checks the rule first and keeps only Fineract's messages;
  `scripts/dev.py` generates passwords that meet it, and CI makes its `.env` the same way.
- *The OpenTelemetry collector and Jaeger weren't running* on the local stack, so the seed's traces
  had nowhere to go. Started again for this check.

**Principles.** _Same settlement-clerk metaphor._ *One stamp, one clerk*: write access is a login
only the gateway has, not a promise. *Check the mandate at the ledger door*: the writer verifies
the approval itself. *Number the voucher before it moves*: idempotency keys turn a retry into a
lookup. *Copy the bank's own arithmetic*: the ledger takes core banking's split rather than
computing its own. *A refusal goes to a person; an outage goes back in the tray.*

**Files.**

- New:
  - `services/gateway/` (solution; `src/Repaydesk.Gateway`: `Program.cs`, `Drainer.cs`, `Outbox.cs`,
    `Mandate.cs`, `Ledger.cs`, `Fineract.cs`, `Odoo.cs`, `Model.cs`, `Settings.cs`; `tests/`;
    `Dockerfile`, `README.md`);
  - `migrations/006_gateway.sql`, `docs/adr/0007-posting-gateway.md`.
- Changed:
  - `adapters/fineract.py` (service logins, the password rule), `adapters/odoo.py` (setup corrects
    accounts), `adapters/chart.py` (charges income, 420000), `seed.py` (creates the logins),
    `records.py` (a split's keys link to their line), `views.py` (the posting's details);
  - `tests/test_records.py`, `tests/contract/test_contracts.py`;
  - `web/src/api.ts`, `pages/LineDetail.tsx` (the posting panel and trail labels), `App.test.tsx`;
  - `compose.yaml` (the `gateway` service; the read-only login for `api`, `directory`, `match`),
    `.env.example`, `scripts/dev.py` and `Makefile` (`env`, `gateway`), `.github/workflows/ci.yml`
    (a gateway job; the stack job posts an approved match), `README.md`.

**Depends on / blocks.** Depends on chunk 4 (the outbox and decisions), chunk 1 (Fineract and
Odoo) and chunk 5 (the case file). Blocks chunk 9: the end-to-end demo and the Today page, which
lists failed postings for a person.

**Verification.** `ruff` clean. `pytest`: 101 passed, two new. Contract tests against Fineract and
Odoo: 9 passed, one new (every account Repaydesk posts to carries its name and type). `vitest`: 25
passed, one new (a posted case file and a failed one). `tsc` clean.

The gateway's own tests, `dotnet test`: 30 passed, warnings as errors.

- The rules: the threshold and a kobo over it, an officer and a supervisor, two officers refused, a
  row naming people who didn't decide, an auditor's approval, a rejection, amounts that don't add up
  or differ from the proposal.
- The ledger entry from Fineract's split, including charges and an overpayment; a split that
  disagrees stops before the ledger.
- The Fineract client against canned responses: found, not found, posted and read back; a closed
  loan or a bad date stops; a missing privilege, a 401, a 5xx or no answer retries; a reversed
  repayment isn't taken as posted.
- Against Postgres with the real migrations:
  - an approved match reaches both books and the line is posted;
  - a crash between the books resumes on time without posting twice, and the row shows what
    Fineract took meanwhile;
  - a forged row and a large payment with one approval are refused;
  - a refusal from core banking waits for a person;
  - a split posts a repayment per loan and one entry;
  - two gateways over 30 payments post each once;
  - an approval wakes the gateway;
  - the database refuses a "posted" row without both books.
- Two deliberate breaks to prove the tests bite: without `SKIP LOCKED` the two-gateway test fails;
  without the look-before-posting the crash test fails.

On the running stack:

- **The waiting payments.** The gateway started and posted the three approved payments waiting
  since chunks 4 to 6, about a second apart. Odoo's loan principal fell ₦1,370,500 and interest
  income rose ₦336,000, exactly Fineract's split; the collection accounts rose by the channels'
  amounts.
- **A live approval.** Tunde approved a ₦363,000 POS payment: posted in both books 3.5 seconds
  after the click.
- **Odoo stopped.** Another approval posted to Fineract and was retried at 10 and 30 seconds. It
  completed on the third attempt once Odoo was back, with exactly one Fineract repayment.
- **The case file** in the browser showed that whole story: the approval, the hand-off, two
  retries, posted, the ledger entry and Fineract's split. No console errors.
- **The loan page,** read live through the read-only login, shows the repayment as posted and
  linked to its line, no longer held.
- **The logins.** The read-only login is refused a repayment (403). The gateway's login is refused
  reading clients, creating one, and undoing a repayment.
- **Jaeger** has each posting as one trace: the database, Fineract's calls, Odoo's calls.

What it does not prove:

- **A split live.** No rule proposes a split today, so splits are proven by the tests only.
- **A failed posting handled by a person.** It shows on the case file; the Today page and a retry
  action arrive in chunk 9.
- **Lists right after a posting.** Borrower and loan lists read the directory's copy, so for up to
  15 minutes a just-posted payment shows in neither "not yet posted" nor the copy's balance. A
  record's own page reads Fineract and is right at once.
- **Load.** The gateway holds a transaction through a second or two of HTTP per posting. Fine for a
  demo; a bank's volume would run several gateways, which the lock already allows.
- **Odoo's own permissions.** The gateway uses Odoo's admin login (see above).

**PR package.**

- Subject: `Chunk 8: the posting gateway (.NET), and a login per service in Fineract`
- Description:
  > Adds the posting gateway, a .NET 10 service and the only writer to Fineract and the ledger.
  > Woken by Postgres when a match is approved, it rechecks the approval against the decisions,
  > posts the repayment to Fineract under the payment's idempotency key, posts a journal entry to
  > Odoo crediting the loan as Fineract split it, and marks the line posted with its audit row, in
  > one transaction holding the outbox row's lock. Writes are looked up by key before they're made,
  > so a crash or retry never posts twice; outages retry with backoff, refusals wait for a person.
  >
  > Each service now has its own Fineract login: read only for the API, directory and matching, and
  > repayments only for the gateway. Also fixes chunk 1's Odoo setup, which left six accounts with
  > Odoo's generic names ("Tax Paid" for loan principal). ADR 0007 records the design.

**Plan.** Milestone 2, rules, approvals and gateway: the .NET posting gateway, and approved matches
visible in Fineract and Odoo.

---

## Chunk 9 — Today, and milestone 2 end to end ✅

_Date: 2026-10-10_

**What & why.** _Metaphor: the morning board in a back office._ Before anyone opens a file, the
team lead looks at the board by the door: what came in overnight, what's waiting for a signature,
what's been sitting unclaimed and for how long, and anything the ledger room sent back. A voucher
sent back is pinned there with the clerk's note, until someone fixes what's wrong and puts it
back in the tray.

Until now, staff landed on the list of payments, and a posting the books refused was visible only
if someone happened to open that payment. This chunk adds the board, and runs the whole milestone
as one story:

- **Today** (`/today`, now where sign-in lands), as in the approved mockup:
  - the latest statement week: money received, the share matched by rule, where the week's lines
    are, each channel;
  - the review queue's size, and how many payments need two people;
  - money no rule could place, by age;
  - the postings: posted today, on the way, retrying, and stopped, with Fineract's or Odoo's
    reason. The navigation shows a red count while any posting has stopped.
- **Retry posting.** Once a person has fixed what stopped a posting (a loan reopened in Fineract,
  an account fixed in the ledger), they send it back from Today or from the payment's case file.
  The row goes back to pending, Postgres wakes the gateway, and the trail records who did it.
- **The demo** (`python scripts/dev.py demo`): a new Northgate Bank statement for today, with
  four repayments quoting live loans (one over ₦500,000), a reversal and a transfer naming nobody.
  It is ingested and matched by the everyday code. Amaka and Tunde approve through the API. When
  the gateway has posted, the demo asks Fineract and Odoo themselves for each repayment and entry.
  `--for-review` stops after matching, so a presenter approves on camera. CI runs it on main.
- **`docs/demo.md`**: the same story to click through, for showing someone.

**Decisions worth recording.**

*The week is the data's, not the calendar's.* Today's week is the seven value dates ending on the
latest line held. Statements arrive a day or more late, and a quiet morning shouldn't read as an
empty week. The queues (review, unmatched money, stopped postings) count everything, whenever it
arrived: work doesn't expire at the end of a week.

*Unmatched money is aged from its value date*, the day it reached the bank, not the day Repaydesk
saw it. That is the age a bank's suspense policy uses, and it doesn't reset if a file is loaded late.

*Retry changes nothing about the approval.* It only puts the row back in the tray. The gateway
rechecks the decisions and looks each write up by key, so a retry can't post twice or post what
wasn't approved. A posting the gateway *refused* (the decisions don't support it) has no retry:
the same decisions would be refused again, and it needs a new proposal. Auditors can't retry.

*The demo goes through the front door.* It approves over HTTP as the web app does, and checks the
books with the read-only login and Odoo's own search, not Repaydesk's database. If the gateway
marked something posted that isn't in a book, the demo fails.

**Principles.** _Same morning-board metaphor._ *Put trouble where people look first*: a stopped
posting is on the landing page, not three clicks deep. *A note with every returned voucher*: the
book's own reason, verbatim. *Fix the cause, then put it back*: retry is a person's decision
after a fix, never a loop. *Show it the way it's used*: the demo is the milestone, run against the
real systems, and CI runs it.

**Files.**

- New: `src/repaydesk/demo.py`; `web/src/pages/Today.tsx`, `components/RetryPosting.tsx`;
  `docs/demo.md`.
- Changed:
  - `approvals.py` (`retry_posting`), `api.py` (`GET /today`, `POST /postings/{id}/retry`),
    `views.py` (`today`; the summary counts stopped postings), `synth/writers.py` (one day of a
    Northgate statement, for the demo; the week's files are byte for byte the same);
  - `tests/test_approvals.py` (two tests);
  - `web/src/api.ts`, `App.tsx` (Today is the landing page), `components/Shell.tsx` (the red count),
    `pages/LineDetail.tsx` (retry on the case file), `pages/Later.tsx`, `styles.css`,
    `App.test.tsx`;
  - `compose.yaml` (the `demo` tool), `scripts/dev.py` and `Makefile` (`demo`),
    `.github/workflows/ci.yml` (the demo replaces the single posted match; Today shows nothing stuck),
    `README.md`.

**Depends on / blocks.** Depends on chunks 2 to 8. Completes milestone 2 (tag `m2`). Blocks
milestone 3: the investigator agent takes the unmatched lines Today ages, and its runs take the
panel Today keeps for them.

**Verification.** `ruff` clean. `pytest`: 103 passed, two new, against Postgres:

- a failed posting sent back by a person: the auditor is refused, a refused posting and a pending
  one can't be retried, a missing one is 404; the retry wakes the gateway (`NOTIFY` seen), keeps
  the attempts and the last error on the row, and is in the audit log;
- Today counts the week (received, by status, by rule, by channel), the queue (and those needing
  two people), unmatched money by age, and postings on the way.

Contract tests against Fineract and Odoo: 9 passed. `vitest`: 27 passed, two new, and one extended:
landing on Today with a stopped, a refused and a retrying posting, with Retry calling the API and
reloading; an auditor sees no Retry; Retry on a failed case file. `tsc` clean; the production build
succeeds. The gateway is unchanged.

On the running stack:

- **The demo**: a statement of six lines, ingested; rules proposed four repayments and the reversal,
  and left the nameless transfer. Amaka approved three; the ₦968,000 payment and the ₦2.5m reversal
  took her first approval and Tunde's second. The gateway posted RD-P219 to RD-P222, and the demo
  found each in Fineract (transactions 10156 to 10159) and in Odoo (RPY/2026/10/0010 to 0013).
  `--for-review` stopped after matching, leaving the proposals in the queue.
- **A stopped posting, handled.** With the gateway stopped, Tunde approved a ₦363,000 payment; its
  loan was then written off in Fineract. Started again, the gateway failed it: "Fineract 400: Loan
  must be Active, Fully Paid or Overpaid". `/today` listed it with that reason and the navigation
  count was 1. After undoing the write-off, Hadiza's retry was refused (403); Amaka's put it back,
  and the gateway posted it about a second later (ledger entry RPY/2026/10/0014). Fineract holds
  one repayment under RD-P44. The trail: approved, handed over, failed, sent back by amaka, posted.
- **The writer refactor**: the week's Northgate files, regenerated with the seed's date, are
  identical to the ones on disk.

What it does not prove:

- **The Today page in a browser on the live stack.** I checked its data through the web app's own
  `/api` and its rendering in the component tests, but didn't sign in to the browser for it: that
  needs the demo password, which stays in `.env`.
- **A posting stopped by Odoo.** Only Fineract's refusal was drilled; Odoo's goes the same way
  through the gateway's error handling (chunk 8's tests).
- **The week window over time.** Run on 10 October, the seeded week (1 to 7 October) shows as 4 to
  10 October, because the demo's statement is dated today; the earlier three days are still in
  Payments.
- **Phone width** for Today; the mockup's layout rules are used, but not checked on a phone.

**PR package.**

- Subject: `Chunk 9: Today, retry for a stopped posting, and milestone 2 end to end`
- Description:
  > Adds Today, the page staff land on: the latest statement week, the review queue, unmatched
  > money by age, and postings (posted today, on the way, retrying, stopped with the books'
  > reason). A person sends a stopped posting back with Retry posting once the cause is fixed; the
  > gateway rechecks the approval and the idempotency key prevents a double post. Postings the
  > gateway refused, and auditors, can't retry.
  >
  > `python scripts/dev.py demo` runs milestone 2 against the stack: a new statement is ingested,
  > matched, approved through the API (two people where needed), posted, and found in Fineract and
  > Odoo. CI runs it on main. `docs/demo.md` is the click-through version.

**Plan.** Milestone 2, rules, approvals and gateway: the end-to-end demo, and the Today screen.
Milestone 2 is complete.

---

## Chunk 9a — Graphite colours ✅

_Date: 2026-10-10_

**What & why.** _Metaphor: a ledger printed in black, with only the stamps in colour._ The web
app moves from the mockup's ledger green to Graphite: near-black ink on warm grey, and no brand
colour at all. The only colour left on screen is status: posted (green), awaiting review (blue),
retrying (amber), stopped (red). The page's job is to show what needs a person, so that is what
the eye finds first. Chosen by the user from three proposals
([colour options](https://claude.ai/artifact/MQ4Td3CSy5HFzHaA9Gd89Q)).

**Decisions worth recording.** Links share the text's colour now, so they are underlined (a muted
underline that darkens on hover); navigation and button-styled links keep none. Status colours are
unchanged from the mockup. Primary buttons are near-black in light mode and near-white in dark.

**Principles.** _Same ledger metaphor._ *Colour means something*: when only status is coloured,
colour always says "look here". *Every colour is a token*: the change is one block of CSS.

**Files.** `services/web/src/styles.css` (theme tokens, link underline).

**Depends on / blocks.** Depends on chunk 5's token system. Blocks nothing.

**Verification.** Every text pairing passes WCAG AA in both themes (text on background 16.2:1,
muted text on panels 6.3:1, button text on buttons 14.7:1 light and 15.3:1 dark). `tsc` clean,
`vitest` 27 passed. The rebuilt `web` container serves it; the sign-in page shows Graphite in dark
mode in Chrome.

What it does not prove: the signed-in screens in a browser (the demo password stays in `.env`), and
light mode on the live stack. The approved mockup keeps its green; it is a record of what was
approved, not the app.

**PR package.**

- Subject: `Chunk 9a: Graphite colours for the web app`
- Description:
  > Replaces the ledger-green theme with Graphite: near-black on warm grey, no brand colour, so
  > status is the only colour on screen. Links are underlined since they share the text colour.
  > One block of tokens in styles.css; WCAG AA in light and dark.

**Plan.** Milestone 2 follow-up, chosen by the user after `m2`.

---

## Chunk 10 — read-only MCP servers for the investigator ✅

_Date: 2026-10-12_

**What & why.** _Metaphor: the records-room window._ An investigator at a bank doesn't wander the
vault. They go to the records-room window and ask a clerk for a file: this borrower, that loan's
ledger card, everyone who banks under "Ifeoma Pharmacy". The clerk hands copies through the
window, with the phone numbers and account numbers inked over. The investigator can ask for
anything relevant, and can't take anything away or write in a single file.

Milestone 3's investigator agent will work out whose loan a payment is meant for when no rule can.
This chunk builds its window, before the agent itself, so the agent's reach is settled first:

- **The bankbook MCP server**, `repaydesk.servers.bankbook`, has eight lookups:
  - a payment line as it arrived;
  - borrowers by name, trading name or group, fuzzily (corrected in chunk 12: the search now says how
    many borrowers matched and when its list is cut short; it used to stop at 8 silently);
  - borrowers by the phone typed on a line;
  - a borrower and a group;
  - a loan, read live from Fineract, with what's due next and the amounts that would fit;
  - how a loan was paid before, and under what name;
  - earlier payments that read like this one, and where they went.
- **The ledger MCP server**, `repaydesk.servers.ledger`: a journal entry by its reference, and
  account balances, read from Odoo.
- **Fuzzy search in Postgres** (migration 007, `pg_trgm`). Payers arrive misspelt, initialled
  ("EZE C"), reordered ("BAKARE BABAJIDE") or as a trading name ("IFEOMA PHARMACY"), and ILIKE
  finds none of those.
- **Usable by people too.** `.mcp.json` starts both servers for Claude Code (or Claude Desktop) in
  the stack, and `python scripts/dev.py mcp` checks them the same way.

**Decisions worth recording.** [ADR 0008](docs/adr/0008-agent-data-access.md).

*Read-only at the source.* The bankbook's database session is read-only; core banking is read
with Fineract's read-only login. The ledger server logs in to Odoo as a new `repaydesk-read` user in
Odoo's read-only accounting group, created by the seed, and Odoo refuses it a journal entry.
Neither server holds a credential that can post.

*Names in, numbers masked.* A payer's name and a narration's words are the evidence, so the model
reads them. Phone and account numbers reach it as their last four digits, in every answer. The one
place a whole number matters, a phone typed at a POS terminal, has its own tool that takes the
line's id and looks the number up itself.

*Live where it decides, copied where it searches.* Searches read the directory, which says when
it was copied. A loan's balance and what's due are read from Fineract live.

*MCP 2.x.* The current major version of the Python SDK (`MCPServer`, formerly `FastMCP`). An
unexpected error in a tool reaches the client as "Error executing tool", without details; a
lookup that finds nothing says so, for the model to act on.

**Found on the way.**

- *Odoo does have a read-only accounting group.* ADR 0007 said Odoo had no fine-grained roles over
  JSON-RPC. That's true for writing (nothing lets a login post only repayments) but not for
  reading. The ADR is corrected in place; the gateway still uses the admin login.
- *An MCP client starts servers with almost no environment.* On Windows the docker CLI then can't
  find its compose plugin and rejects `--rm`. The smoke check passes the caller's environment, as
  Claude Code does.

**Principles.** _Same records-room metaphor._ *Hand copies through a window*: the agent asks, it
never holds a key. *Ink over what isn't evidence*: numbers that reach a person or an account stay
masked. *The same window for people and the agent*: a person in Claude Desktop gets the answers the
agent gets. *Settle the reach before the reasoning*: tools and their limits come before the agent.

**Files.**

- New:
  - `src/repaydesk/bankbook.py`, `privacy.py`;
  - `servers/__init__.py`, `servers/bankbook.py`, `servers/ledger.py`;
  - `migrations/007_search.sql`;
  - `tests/test_bankbook.py`;
  - `scripts/mcp_smoke.py`, `.mcp.json`, `docs/adr/0008-agent-data-access.md`.
- Changed:
  - `adapters/ports.py`, `odoo.py`, `memory.py`: `read_entry`, and Odoo's `ensure_read_user`;
  - `seed.py`: creates the Odoo read-only login;
  - `tests/contract/test_contracts.py`: two tests;
  - `pyproject.toml` (`mcp`), `compose.yaml` (`bankbook` and `ledger` tools; the seed's new login),
    `.env.example`, `scripts/dev.py` and `Makefile` (`mcp`), `.github/workflows/ci.yml`, `README.md`;
  - `docs/adr/0007-posting-gateway.md`: Odoo's roles, corrected.

**Depends on / blocks.** Depends on chunk 7 (the directory) and chunk 8 (the read-only Fineract
login). Blocks chunk 11, the investigator agent, which reads only through these servers.

**Verification.** `ruff` clean. `pytest`: 112 passed, 9 new.

- Masking: phones in three spellings, account numbers, and a bank's reference left alone.
- Search finds a borrower from their reordered name, an initialled one, their trading name, and
  the trading name buried in a narration. An exact name scores 1.0, and a branch filter excludes
  them elsewhere.
- A POS line's typed phone finds its borrower, and the number appears in no answer.
- A loan read through the tools lists its next instalment among the amounts that would fit.
- History shows the lines Repaydesk matched to a loan with the payer's name, and core banking's
  repayments.
- The session refuses a `DELETE`.
- Over the MCP protocol (in memory), both servers offer only read-only tools, and a missing loan
  or journal entry comes back as a message.

Contract tests against Fineract and Odoo: 11 passed, two new. A journal entry reads back line by
line (both adapters), and Odoo's read-only login reads entries and balances but is refused a
posting. The gateway's tests (30) pass with the new migration.

On the running stack:

- **The migration** applied on the API's start.
- **The seed** created the Odoo login ("Odoo login repaydesk-read: read-only accounting").
- **`python scripts/dev.py mcp`** started both servers over stdio through
  `docker compose run`, as `.mcp.json` does. It listed 8 and 2 tools, all read-only, and answered
  a search and the balances; phones came back masked.
- **A real hard case through the tools.** Take ₦605,000 "TRF FROM IFEOMA PHARMACY":
  - The search returns two borrowers trading as Ifeoma Pharmacy (Onitsha and Ikeja), both scoring
    1.0.
  - Read live, the Onitsha loan owes exactly ₦605,000. The Ikeja loan owes ₦58,080, with a
    monthly history at that amount.
  - That is the evidence the agent will weigh.

What it does not prove:

- **That a model uses the tools well.** That is chunk 11's agent, and its evals.
- **Search at a bank's size.** It ran on 2,039 borrowers. The GIN indexes are there for more, but
  query plans weren't checked at scale.
- **Claude Desktop itself.** Only the same stdio launch was checked, not the app.
- **The HTTP transport** (`--http`), which nothing uses yet.

**PR package.**

- Subject: `Chunk 10: read-only MCP servers for the investigator (bankbook, ledger), fuzzy search`
- Description:
  > Adds two MCP servers (Python MCP SDK 2.x) that the milestone 3 investigator agent will read
  > through, and that Claude Code or Desktop can use via `.mcp.json`. `bankbook` looks up payment
  > lines, borrowers (trigram fuzzy search over names, trading names and groups), loans live from
  > Fineract, and payment history. `ledger` reads journal entries and balances from Odoo. Every
  > tool is read-only at the source: a read-only database session, Fineract's read-only login, and
  > a new read-only Odoo login the seed creates. Phone and account numbers are masked in every
  > answer; the one tool that needs a phone looks it up server-side. ADR 0008.

**Plan.** Milestone 3, investigator agent, MCP and evals: the read-only MCP servers.

---

## Chunk 11 — the investigator agent ✅

_Date: 2026-10-12_

**What & why.** _Metaphor: the senior officer's desk, and the supervisor's tray._ A payment
nobody can place goes to a senior officer, who pulls files at the records window, weighs what they
show and writes a short note:
- "Ifeoma Pharmacy is two customers, but only the Onitsha one owes ₦605,000; no history to
  confirm; fairly sure."
- or "two Afolabis at Ibadan, same amount; I can't tell. Phone the branch."

The note goes in the supervisor's tray. The officer never posts anything, and the supervisor reads
the note before signing.

This chunk adds the officer, for the lines rules leave behind:

- **Triage**, by Claude Haiku 5.5: what kind of payment the narration describes. A person, a
  business, a group, someone paying for someone else, a reference, Pidgin.
- **Investigation**, by Claude Sonnet 5.5, through the bankbook MCP server's read-only lookups
  (chunk 10) and nothing else. It answers in a fixed shape:
  - a match (one loan or a split), not a repayment, or suspense;
  - allocations in kobo;
  - a confidence and a reason for the reviewer;
  - its findings, each for, against or neutral;
  - the candidates it considered.
- **Repaydesk's checks**, before anything is saved:
  - the amounts add up to the payment to the kobo;
  - every loan is active in core banking, read live;
  - no loan appears twice;
  - the confidence clears 0.7.

  A failed check turns the answer into suspense, with the reason recorded.
- **A proposal for people**, by `agent:investigator`, in the same review queue as a rule's, under
  the same limits and two-person rule. The review card shows the agent's reason first, then its
  findings marked ✓ for, ✕ against or – neutral, then what else it considered.
- **Every run kept** in `agent_runs` (migration 008): the triage, each tool call and what came
  back, the answer, the checks, tokens per model and cost. `python scripts/dev.py investigate`
  runs it.

**Decisions worth recording.** [ADR 0009](docs/adr/0009-investigator-agent.md), and the user's
choices on 12 October:

- *The Anthropic SDK's tool runner, not the Claude Agent SDK.* The plan named the Agent SDK. The
  investigator needs exactly eight read-only tools; the Agent SDK brings Claude Code's file, shell
  and web tools to switch off, and a runtime in the container. The tool runner loops over the
  bankbook's tools, converted from MCP with the SDK's helpers, and runs unchanged on Bedrock.
- *Haiku 5.5 for triage*, not Haiku 4.5 as planned: the current Haiku, at a tenth of the price.
- *Prompted for doubt.* The prompt describes how payments arrive here and how to weigh them. The
  amount and the payer's history are strong evidence; a common name alone is weak. Two equal fits
  mean suspense, because a wrong match moves someone else's money, and suspense costs a phone call.
- *Answers are checked, never trusted.* The structured answer is validated by Repaydesk's own code
  against core banking before it becomes anything.
- *A failed run changes nothing.* An outage or a refusal is recorded on the run, and the line stays
  open for the next attempt. On the Claude API, requests carry server-side fallbacks, so a declined
  request is rerun on a fallback model in the same call.
- *The key.* The Automation Hub's Claude API key, copied into Repaydesk's git-ignored `.env`
  without being printed (the user's choice).

**Found on the way.** The `anthropic[mcp]` extra wouldn't resolve beside MCP 2.x, but nothing in
it is needed: the SDK's MCP helpers work with the 2.x client as installed.

**Principles.** _Same officer-and-tray metaphor._ *The officer writes a note; the supervisor
signs*: an agent proposes, a person decides. *Show your working*: every lookup, finding and
check is on the run. *When in doubt, phone the branch*: suspense is a good answer. *Check the
arithmetic yourself*: amounts and loan states are verified by code, not by the model's word.

**Files.**

- New:
  - `src/repaydesk/investigator.py`, `investigate.py`;
  - `migrations/008_agent_runs.sql`;
  - `tests/test_investigator.py`;
  - `docs/adr/0009-investigator-agent.md`.
- Changed:
  - `tests/conftest.py`: the book-in-core-banking helper and a `placed_week` fixture, shared;
  - `tests/test_records.py`, `tests/test_bankbook.py`: use them;
  - `web/src/evidence.ts` and `evidence.test.ts`: an agent's reason and findings in the review
    queue;
  - `pyproject.toml` (`anthropic`), `compose.yaml` (`investigate`), `.env.example`,
    `scripts/dev.py` and `Makefile` (`investigate`), `README.md`.

**Depends on / blocks.** Depends on chunk 10 (the MCP servers) and chunks 4 and 6 (proposals and
the review queue). Blocks chunk 12: the eval set and its report, which measure this agent. Also
blocks chunk 13, the Agent runs screen.

**Verification.** `ruff` clean. `pytest`: 116 passed, 4 new, without network or a key.

- Repaydesk's checks: a kobo out, a loan listed twice, an unknown loan, no allocations, confidence
  at 0.69, and a doubtful "not a repayment" each become suspense. A good match becomes a match; a
  confident "not a repayment" becomes an exclusion.
- A recorded match is a pending proposal by `agent:investigator`, with the run's id in its
  evidence. The line is "proposed" and nothing is in the posting outbox. The run holds its steps
  and cost ($0.003 for 1,000 tokens in and 100 out), and the audit names the agent.
- Suspense marks the line. A failed run leaves the line open, with the error. An answer for a line
  a rule or person placed meanwhile is kept as history only.
- One whole investigation against a scripted model (a stub HTTP transport, no network), through
  the real tool runner and the real MCP server:
  - the triage goes to Haiku, the investigation to Sonnet;
  - the investigator is offered the bankbook's eight tools and nothing else;
  - requests carry fallbacks and effort `medium`;
  - the search result reaching the model holds the right loan;
  - the run records the step and tokens per model.

`vitest`: 28 passed, one new. `tsc` clean. The gateway's 30 pass with migration 008.

On the running stack, with the real models:

- **"TRF FROM IFEOMA PHARMACY", ₦605,000.**
  - **The lookups.** Two of them, in parallel: a search for the trading name and earlier payments
    under it.
  - **Proposed.** LN-2026-000717 at 0.85. The reason: two borrowers trade as Ifeoma Pharmacy, and
    only the Onitsha one owes exactly ₦605,000.
  - **Flagged.** A third borrower whose arrears are also ₦605,000 but whose trading name doesn't
    fit (Iya Ifeoma Foods), as neutral evidence.
  - **Noted.** No payment history, which is why it held back from 0.9.
  - **Checks.** All five passed.
  - **Cost.** 2.6 cents.
- **Fourteen more lines**, 12 locally and 2 in the `investigate` container: all proposed, at
  confidence 0.72 to 0.93.
- **Scored against the truth file:** 15 proposed, 15 correct, 0 wrong. They included names only,
  trading names, mistyped cash references and a POS reference.
- **Cost:** 15 runs for $0.37 in all, about 2.5 cents each.

What it does not prove:

- **Doubt.** All 15 were the oldest open lines, and none called for suspense. The unresolvable
  cases (the blank POS lines, two equal Afolabis), groups and splits weren't drawn. Chunk 12's eval
  set picks the hard cases on purpose, suspense-worthy ones included.
- **A person approving an agent's proposal.** The review queue shows the findings (tested in
  vitest), but no live approval was made in this chunk. The approval path is the same as a rule's.
- **Bedrock.** The client is written from the SDK's documented `AsyncAnthropicBedrockMantle`, but
  it hasn't been run against Bedrock.
- **A changed line** under an agent: the "superseded" path is tested against a scripted model,
  not seen live.

**PR package.**

- Subject: `Chunk 11: the investigator agent (Haiku triage, Sonnet investigation, checked answers)`
- Description:
  > Adds the investigator agent for payments no rule could place. Claude Haiku 5.5 triages the
  > narration; Claude Sonnet 5.5 investigates through the bankbook MCP server's read-only tools (the
  > Anthropic SDK's tool runner, not the Claude Agent SDK), and answers in a fixed shape. Repaydesk
  > checks the answer (amounts add up, loans active, confidence over 0.7) before it becomes a
  > proposal in the review queue, under the same approval rules as a rule's; doubt becomes
  > suspense. Every run is recorded with its tool calls, checks and cost. On the live stack, 15
  > lines investigated, 15 correct, about 2.5 cents each. ADR 0009.

**Plan.** Milestone 3, investigator agent, MCP and evals: the investigator on the Claude models.

---

## Chunk 12 — the investigator's eval ✅

_Date: 2026-10-12_

**What & why.** _Metaphor: the practice file._ A bank trains a new investigator on a drawer of old
cases whose answers are known. They work each one cold, and a senior marks them:
- right;
- wrong, the ones that would have moved someone else's money;
- held for a phone call, cautious but harmless.

The marks are only worth something if the drawer stays the same between tries, and if the senior
marks to the kobo.

Chunk 11's agent got 15 of 15 right on the oldest open lines, but those were the easy ones. This
chunk builds the drawer and the marking:

- **The cases**: `evals/cases.json`, and `cases.md` to read. 60 hard payments from the seeded week:
  - every group leader, third party, mistyped reference, payer-only and Pidgin payment, and the
    split;
  - the three nobody could place;
  - 6 each of names, trading names and cash-sheet names.

  The expected answers are the generator's truth, never a model's.
- **The runner**, `repaydesk.evals`: each case through the real investigator, the real MCP tools and
  Repaydesk's real checks, against the stack, but dry. Nothing is written, and the review queue is
  untouched. Four at a time, each with its own database session.
- **The grading**:
  - correct: the same loans and amounts to the kobo, or suspense for the unplaceable;
  - wrong: any other match, or a repayment called "not a repayment";
  - held: suspense for a placeable payment;
  - failed: no answer.

  The report puts **wrong** first, with a 95% interval on accuracy, a table by kind of payment, and
  every case with its reason, tools and cost.
- **On demand in CI.** The **Investigator eval** workflow, manual only, seeds the stack exactly as
  the cases expect and uploads the report. It needs the `ANTHROPIC_API_KEY` repository secret.
- **One round of fixes, from what the eval found:**
  - **The search hid how many borrowers matched.** `search_borrowers` returned 8 and said nothing
    about the rest. Told to check every candidate, the agent checked the 8 it saw: "Only Obinna
    Ibe's loan fits ₦121,000", out of 46 borrowers trading as Success Motors Spares. The search now
    says how many matched, shows up to 25, and says plainly when more weren't shown. Chunk 10's
    section is corrected.
  - **`loans_fitting`**: one call checks an amount against every candidate's loans, read live.
    Saying "no other loan fits" no longer costs eight lookups.
  - **`nearby_loan_refs`**: the real loans one slip away from a quoted reference (a digit changed,
    or two swapped), with each borrower's name and branch.
  - **Three prompt rules**:
    - check every candidate before claiming only one fits;
    - a quoted reference is evidence only if its borrower fits the payer;
    - a split chosen because its amounts add up is a guess, and goes to suspense.

**Decisions worth recording.**

*Exact grading, from the generator.* The truth was written when each payment was made up, so the
grader is a comparison, not a judge: no model marks the model. The price is that the truth can't
see ambiguity it created. It gives one trading name to dozens of borrowers, so some payments it
calls placeable aren't, from the payment alone. Holding those shows as "held", not "wrong", and
the README says so.

*Wrong counts more than right.* A held payment costs a phone call. A wrong one, if a reviewer
misses it, moves someone else's money. The report and the runs table lead with it.

*Dry runs.* The eval never writes a proposal, so it can run against the stack people use. A
failed case (an outage, a refusal) is "failed", not "wrong".

*On demand, not on every push* (the user's choice): about $2.40 a run.

**Found on the way.**

- *The search cut lists short silently*, and the agent believed it had seen everyone. See above.
- *The API key's credit ran out* during the third run, after 26 cases. The other 34 failed with
  "Your credit balance is too low". The run's report was removed as incomplete. The same key is
  the Automation Hub's, so the hub's model calls fail too until credit is added.
- *I misread two wrong answers as circular evidence*: the agent citing its own earlier guesses.
  They weren't. The earlier payments it cited were matched by the virtual-account rule, which is
  certain. Those two are close calls, a relative paying with a family member's name.

**Principles.** _Same practice-file metaphor._ *Keep the drawer fixed*: the same 60 cases every
run, chosen by rule, so a change shows. *Mark to the kobo*: exact comparison, no judge. *Count the
dangerous mistakes first*: wrong before right. *Say what the marks can't see*: noise (±12 points)
and the truth's own blind spots are in the report.

**Files.**

- New:
  - `src/repaydesk/evals.py`;
  - `tests/test_evals.py`;
  - `evals/README.md`, `cases.json`, `cases.md`;
  - `evals/reports/` (two runs, Markdown and JSON lines);
  - `.github/workflows/evals.yml`.
- Changed:
  - `bankbook.py`: `loans_fitting`, `nearby_loan_refs`; the search reports its total and says
    when a list is cut;
  - `servers/bankbook.py`: the two tools;
  - `investigator.py`: three prompt rules;
  - `tests/test_bankbook.py`: three tests;
  - `tests/test_investigator.py`: the tool set;
  - `scripts/dev.py` and `Makefile` (`eval`), `README.md`;
  - `docs/adr/0009-investigator-agent.md`: the eval's results.

**Depends on / blocks.** Depends on chunk 11. Blocks chunk 13 (the Agent runs screen) only in that
it shows what runs look like; milestone 3's demo can now quote a measured number.

**Verification.** `ruff` clean. `pytest`: 122 passed, 6 new, none calling a model.

- Grading to the kobo: a kobo out is wrong, an exclusion of a repayment is wrong, a split in either
  order is right, and suspense is right only for the unplaceable.
- The interval for 31 of 60: 39% to 64%.
- The summary keeps wrong apart from held, and the report survives a pipe in a reason.
- The committed `cases.json` is exactly what `select` makes from the seeded truth.
- `loans_fitting` finds a loan's instalment among six candidates, by loan or by borrower.
- `nearby_loan_refs` finds a loan one slip away.
- A search cut to 2 of many says how many more match.

`vitest` 28 passed.

The runs, against the local stack:

| Run | Correct | Wrong | Held | Failed | Cost |
|---|---:|---:|---:|---:|---:|
| First, the chunk 11 agent | 31 | 5 | 24 | 0 | $2.13 |
| With the two tools and three rules | 30 | 3 | 27 | 0 | $2.42 |
| With the search fix (incomplete) | 15 of 26 run | 0 | 11 | 34 | $1.76 |

- **The second run.** Mistyped references went from 4 of 6 to 6 of 6. The two wrong group splits
  are now held, though so are two that had been right. The 3 still wrong:
  - "Success Motors Spares", caused by the cut-off search;
  - two relatives paying with a family member's name.
- **The third run.** Of its 26 cases, none were wrong. Success Motors became an honest hold: "46
  borrowers trade as Success Motors Spares … at least three Abuja Wuse loans fit ₦121,000 equally."

What it does not prove:

- **The search fix over the whole set.** The third run stopped at 26 of 60 when the credit ran
  out; it has to be run again in full.
- **Improvement beyond the cases.** The fixes were diagnosed on these same 60 cases, so the second
  run may flatter them. There is no held-out set yet, and the differences between runs (31 against
  30) are inside the noise.
- **Doubt, well.** Only 3 cases are truly unplaceable (all 3 held, every run). The eval can't yet
  say much about the agent declining when it should, beyond the trading-name collisions.
- **The workflow itself.** It hasn't run: it needs the repository secret, and credit.
- **The local numbers are on a used stack.** It holds proposals and postings from earlier chunks,
  and its "today" is 12 October. A fresh stack, as the workflow builds, may differ by a few cases.

**PR package.**

- Subject: `Chunk 12: the investigator's eval (60 hard cases, exact grading), and what it found`
- Description:
  > Adds a fixed eval for the investigator agent: 60 hard payments from the seeded week, run dry
  > through the real agent, MCP tools and checks, and graded exactly against the generator's truth
  > (correct to the kobo, wrong, held, failed). The report leads with wrong answers and gives a 95%
  > interval. `python scripts/dev.py eval` locally; a manual GitHub Actions workflow with the API
  > key as a secret. The first runs found a search that silently cut its results to 8 (fixed: it
  > now reports the total), and led to two tools (`loans_fitting`, `nearby_loan_refs`) and three
  > prompt rules. Wrong answers went from 5 to 3 over the full set, and none in the partial third
  > run.

**Plan.** Milestone 3, investigator agent, MCP and evals: the eval set of hard lines, and the
report.

---

## Chunk 12a — the UI refresh (design A) ✅

_Date: 2026-10-12_

**What & why.** _Metaphor: a well-kept ledger room._ White paper, black ink, and colour only where
it means something: a red tab on the file that needs a person, a green stamp on what's done, the
investigator's notes on cream paper. The room is calm, so the one red tab is impossible to miss.

The user asked for a refreshed UI after `DESIGN.md`, an editorial, near-black-on-white system, and
chose direction A from two mockups ([canvas](https://claude.ai/artifact/LZv2d9Q8N8DVwHT1z6x46B),
page A). Direction B, black and violet, after `revolut/DESIGN.md`, was the other. This chunk
rebuilds the web app in A:

- **A new stylesheet**, built on the design's tokens:
  - white canvas and near-black ink, in Hanken Grotesk, a free stand-in for the licensed Haas;
  - type at weights 400 and 500 only;
  - a near-black primary button beside a white outlined one, with 12px corners;
  - square-cornered status tags;
  - hairline tables and a segmented filter.

  The old variable names still resolve, so every page took the look without its own edit.
- **A two-row top bar** in place of the sidebar:
  - **Row one:** the bank's name, search, and, on the right, the Prototype tag beside a boxed
    account panel (initials, name, role and limit, Sign out). That placement was the user's
    request after the first build, where the profile had wrapped to the left under the bar.
  - **Row two:** the tabs.
  - **On a phone:** a menu button.
- **Today, as the mockup:**
  - the week in a navy card;
  - a coral "needs a person" card when a posting stopped, or a forest "Nothing stuck" card;
  - four tinted tiles: awaiting review, not matched yet by age, posted today, and the
    investigator's last seven days;
  - the investigator's latest note on cream.

  `GET /today` gains the investigator's figures and that note.
- **The review queue**: the investigator's proposals show on cream; a rule's on a soft grey.
- **The case file**: a coral banner at the top when a posting stopped, holding the Retry button.

**Decisions worth recording.**

*Colour means the same thing everywhere*:
- navy: the headline numbers;
- coral: needs a person;
- forest: done;
- cream: the investigator's own words;
- peach, yellow and mint: queue states, with ink text on them.

The design file uses its signature colours for rhythm; a working tool uses them for meaning.

*Light only, for now.* The design has no dark mode, and Graphite (chunk 9a) is replaced. A dark
version of A can come later.

*Kept from the old look:* keyboard focus rings. The design documents no hover states, and only
focus is kept. A tool needs to show where the keyboard is.

*One Retry per screen.* On Today, the coral card links down to the stopped postings, and Retry
stays in that table. On the case file, Retry moves from the posting panel to the banner.

**Principles.** _Same ledger-room metaphor._ *Calm by default, loud on purpose*: white and ink
everywhere, colour only for a state. *Who you are, apart from what you do*: the account sits boxed
on the right, away from the page's controls. *One action, one place.*

**Files.**

- New: `DESIGN.md` (the reference).
- Changed:
  - `services/web/src/styles.css` (rewritten), `index.html` (fonts);
  - `components/Shell.tsx` (the two-row top bar), `components/RetryPosting.tsx` (a white button on
    coral);
  - `pages/Today.tsx` (the mockup's layout), `pages/Review.tsx` (cream for the investigator),
    `pages/LineDetail.tsx` (the coral banner), `api.ts`, `App.test.tsx`;
  - `services/core/src/repaydesk/views.py` (the investigator in `/today`),
    `tests/test_approvals.py`;
  - `README.md`.

**Depends on / blocks.** Depends on chunks 5 to 12 (the screens it restyles). Blocks chunk 13,
whose Agent runs screen is drawn in the same mockup.

**Verification.** `ruff` clean. The Today API test checks the investigator's figures (none in an
empty schema). `tsc` clean, `vitest` 28 passed (the case file's test now checks the coral banner),
and the production build succeeds.

In Chrome, signed in by the user on the running stack:
- **Today**, as the mockup: the navy week card (₦52,624,014; 219 lines; 62% by rule) and the forest
  "Nothing stuck". The tiles show 230 awaiting review and ₦27.7m not matched. The investigator's
  latest note is run 15, ₦968,000, at 88%, followed by the by-channel table and the flow bar.
- **The header**, after the user's note: the account panel is boxed on the right with the Prototype
  tag, and the tabs sit on their own row.
- **The review queue**: the segmented filter, the cards, and the yellow "needs two people" tag.

Found and fixed on the way: the right-aligned table headings took the number font.

What it does not prove:

- **A stopped posting's coral card and banner on the live stack.** None is stopped right now.
  The case file's banner is covered by the test, and Today's coral card by its tests' stopped
  posting.
- **Phone width in a real browser.** The mockup's phone screen guided the CSS, but the app wasn't
  checked at 390px.
- **Borrowers, Loans, Ingestion and Audit, screen by screen.** They took the new look from the
  stylesheet. The Audit log was seen in passing, and the others weren't opened.

**PR package.**

- Subject: `Chunk 12a: the UI refresh after DESIGN.md (direction A)`
- Description:
  > Rebuilds the web app's look after DESIGN.md: white canvas, near-black ink, weights 400/500,
  > a near-black primary button beside a white outlined one, and colour that always means one
  > thing (navy numbers, coral needs a person, forest done, cream the investigator's words). A
  > two-row top bar replaces the sidebar, with the signed-in account boxed on the right. Today
  > follows the approved mockup, with the investigator's week and latest note; the review queue
  > shows the investigator's proposals on cream; a stopped posting gets a coral banner with
  > Retry on its case file.

**Plan.** Milestone 3 follow-up, chosen by the user: the UI refresh before the Agent runs screen.

---

## Chunk 13 — Agent runs ✅

_Date: 2026-10-12_

**What & why.** _Metaphor: the investigator's working file._ When a senior officer's note lands in
the supervisor's tray, a good supervisor can ask for the working file:
- what the officer pulled from the records room, and what each file said;
- what they concluded, and why;
- what the checking clerk verified before it went further.

The note is short; the file is complete.

Chunk 11 recorded every run, but nobody could see one without a database. This chunk adds the file:

- **Agent runs** (`/runs`): every investigation, newest first, with its payment, outcome,
  confidence, lookups, cost, and where its proposal stands now (pending, approved or rejected).
  Filters by outcome, and a running total of runs and cost.
- **A run** (`/runs/:id`), as drawn in the refresh mockup:
  - **Summary:** a navy card with the outcome, confidence, lookups, cost and checks passed.
  - **The lookups:** the triage, then each lookup with its arguments and, a click away, exactly
    what came back.
  - **The answer:** on cream, with each finding marked ✓, ✕ or –, and the candidates considered.
  - **The checks:** Repaydesk's own, passed or failed.
  - **The outcome:** the payment, and what people have decided about the proposal since.

  A failed run shows its error in coral.
- **Links in:**
  - Today's latest note: "See how it decided".
  - Every review card for an investigator's proposal: "How it decided (run N)".
  - The proposal on a payment's case file.
  - The investigator's tile on Today: "All runs".
- **The API:** `GET /runs` (paged, filterable) and `GET /runs/{id}`.

**Found on the way.** *A run's start time was its save time.* `agent_runs.started_at` defaulted to
the moment of the insert, which `record()` makes after the investigation, so every run took "0
seconds". The investigator now notes when a run began and saves that. The page shows a duration
only when there is one, because runs saved before the fix have none. (Corrected in chunk 15: the end time
was still the database's `now()`, on another clock, so a run could end a fraction of a second
before it began. Both times now come from the application.)

**Decisions worth recording.** *The working file is for everyone who can read the queue,* auditors
included: it shows what the agent saw and why it concluded, which is what an auditor needs. *The
lookups' answers are shown as they came back,* masked as the agent saw them (ADR 0008), behind a
click so the page reads as a story first.

**Principles.** _Same working-file metaphor._ *Show your working, all of it.* *The note first,
the file a click away.* *Close the loop*: a run says what people did with its proposal.

**Files.**

- New: `web/src/pages/Runs.tsx`, `Runs.test.tsx`.
- Changed:
  - `views.py` (`runs`, `run`), `api.py` (`GET /runs`, `GET /runs/{id}`);
  - `investigator.py` (a run's real start);
  - `tests/test_approvals.py` (one test), `tests/test_investigator.py`;
  - `web/src/api.ts`, `App.tsx` (the routes), `components/Shell.tsx`, `pages/Later.tsx`;
  - `pages/Today.tsx`, `Review.tsx`, `LineDetail.tsx` (the links);
  - `README.md` (milestone 3 done).

**Depends on / blocks.** Depends on chunks 11 and 12a. Completes milestone 3 (tag `m3`). Blocks
milestone 4, collections, only in that its drafting agent's runs will appear here too.

**Verification.** `ruff` clean.

- `pytest`: 123 passed, one new. Two runs recorded (a match and a suspense) read back through the
  API, newest first, with counts and an outcome filter (an unknown outcome is refused). One run
  reads back whole: the triage, its lookup, the answer, the checks and its pending proposal. After
  Tunde approves the proposal, the run shows his approval. A missing run is 404, and no token is
  401.
- `vitest`: 30 passed, two new. The list shows runs newest first and filters by outcome. A run
  shows its lookups, triage, findings, checks ("2 of 2"), Tunde's approval and no review button
  once decided, and the lookup's answer opens a click away.
- `tsc` clean.

On the running stack (signed in by the user), read as text because Chrome's window wasn't
visible for screenshots:
- `/runs` lists the 15 live runs, all proposed, $0.37 in all.
- `/runs/1` reads as designed: Ifeoma Pharmacy, the two lookups, the answer and its four findings,
  five of five checks, proposal 229 pending, tokens per model.
- No console errors.

What it does not prove:

- **The run pages in a screenshot.** The text and the tests were checked, but not the pixels; the
  layout follows the mockup's, in the same stylesheet.
- **A suspense or failed run live.** None exists on the stack. Both are covered by the tests.

**PR package.**

- Subject: `Chunk 13: Agent runs: every investigation, whole, and links to it`
- Description:
  > Adds the Agent runs screens: a list of every investigation (outcome, confidence, lookups,
  > cost, and where its proposal stands now) and a page per run showing the triage, each lookup and
  > what came back, the answer with its findings, Repaydesk's checks, and what people decided.
  > Linked from Today, the review queue and the case file. Also records a run's real start time
  > (it was the save time). Completes milestone 3.

**Plan.** Milestone 3, investigator agent, MCP and evals: the agent runs screen. Milestone 3 is
complete.

---

## Chunk 14 — collections foundations ✅

_Date: 2026-10-12_

**What & why.** _Metaphor: the credit officer's rule book and morning list._ Before anyone at a
microfinance bank picks up a phone about a late loan, two things exist:
- **A rule book:** who may be called, when, how often, what must be said, and what must never be
  said.
- **A morning list:** the late loans, sorted by how late. Anyone who has promised to pay is left
  alone until their date, and anyone who broke a promise is on top.

Milestone 4 asks late borrowers to pay with drafted messages a person approves. This chunk writes
the rule book and the list, before any message:

- **The policy**, [`docs/collections-policy.md`](docs/collections-policy.md):
  - only the borrower is contacted, between 08:00 and 18:00, Monday to Saturday;
  - at most two messages per loan in seven days;
  - every message names the loan, the exact amount due, how to pay, and an offer to talk;
  - never threats, shame, links, or a request for a PIN or BVN;
  - tone by how late the loan is;
  - over 90 days goes to people, not messages.
- **Its checkable rules in code**, `repaydesk.collections.policy`: eleven checks every draft will
  pass through, from scope and frequency to the amount written in the message (₦60,500, N60,500.00,
  NGN 60500 and "60,500 naira" all count).
- **Arrears buckets and the morning list**, `repaydesk.collections.queue`. A loan is due a message
  when it is 1 to 90 days late with arrears, has no promise still in date, no draft waiting, and
  fewer than two messages this week. Broken promises come first.
- **The schema** (migration 009): `collection_messages` (draft, approved, rejected, sent, failed),
  `inbound_messages` (replies), and `promises` (open, kept, broken, cancelled). An agent run can now
  be about a loan, not only a payment line, for the drafting agent.
- **A portfolio manager**, Ngozi Eze (`ngozi`): approves collections messages and never payments,
  not even a first approval. Supervisors may do both. Payment decisions now check "decides
  payments" (officers and supervisors) rather than "isn't an auditor".
- **The Collections screen** (`/collections`, `GET /collections`):
  - the four buckets as tiles, with recovery in coral because it's for people;
  - drafts waiting and promises open, kept and broken;
  - the list of who is due a message today, filtered by kind.

**Decisions worth recording.**

- *A bank's own policy, stated as such.* It is written to be firm and fair. A real bank would align
  it with its regulator's rules on debt recovery; the document says so rather than citing rules it
  can't vouch for.
- *Checks for what code can judge; people for the rest.* Code checks the amount, the reference,
  the hours, the frequency, threats and links. Tone and the offer to talk are the agent's
  instructions and the manager's eye.
- *A promise is respected.* A loan with an open promise isn't messaged before the promised date.
- *Approval for messages is a separate right.* An auditor and a portfolio manager both have no
  payment limit, but only the manager (and a supervisor) approves messages.

**Principles.** _Same rule-book metaphor._ *Write the rules before the first word is sent.*
*Respect a promise until its date.* *The latest are people's work*: over 90 days, no drafted
messages. *A right per action*: approving money and approving words are different jobs.

**Files.**

- New:
  - `docs/collections-policy.md`;
  - `src/repaydesk/collections/` (`__init__.py`, `policy.py`, `queue.py`);
  - `migrations/009_collections.sql`;
  - `tests/test_collections.py`;
  - `web/src/pages/Collections.tsx`, `Collections.test.tsx`.
- Changed:
  - `staff.py` (the manager role, `decides_payments`, `decides_messages`), `approvals.py`;
  - `api.py` (`GET /collections`);
  - `tests/test_approvals.py`;
  - `web/src/api.ts`, `App.tsx`, `components/Shell.tsx`, `components/RetryPosting.tsx`,
    `pages/Review.tsx`, `SignIn.tsx`, `Later.tsx`;
  - `README.md`.

**Depends on / blocks.** Depends on the directory (chunk 7) and agent runs (chunk 11). Blocks
chunk 15, the drafting agent, which writes the drafts this list asks for.

**Verification.** `ruff` clean.

- `pytest`: 131 passed, 8 new.
  - **The policy:** a good message passes all 11 checks. Police, "your family", a BVN, a link,
    capitals and two exclamation marks are each caught alone. A wrong amount, a missing reference
    or a missing name fail, and four ways of writing the amount pass.
  - **Scope and timing:** over 90 days is out of scope, a third message in a week fails, a promise
    still in date blocks, and a lapsed one doesn't. Contact hours hold, and Sunday rolls to Monday
    08:00.
  - **The morning list:** a promised loan, a loan with a draft waiting and a twice-messaged loan
    are left out. A broken promise comes first, as its own kind.
  - **The manager:** can't approve a payment. The API serves the manager's morning, and refuses
    an unknown bucket and a missing token.
- The gateway's 30 tests pass with migration 009.
- `vitest` 31 passed, 1 new: the buckets, the broken promise first, the manager's role in the
  account panel, and the kind filter. `tsc` clean.
- **On the stack:** the API applied migration 009 and created Ngozi as a manager.

What it does not prove:

- **The Collections screen live.** The user was browsing in the signed-in tab, so I didn't take
  it over; the screen is covered by its test.
- **One test run had 7 failures** I couldn't reproduce in two further full runs, and didn't
  capture. CI will run the suite again; if it recurs, it gets diagnosed.
- **The bank's policy against a regulator's rules.** It is the demo bank's own.

**PR package.**

- Subject: `Chunk 14: collections foundations: the policy, its checks, buckets and the morning list`
- Description:
  > Adds the collections policy (docs/collections-policy.md) and its checkable rules in code, the
  > arrears buckets, and the morning list of loans due a message (promises respected, broken ones
  > first), with the schema for messages, replies and promises to pay. Adds a portfolio manager role
  > that approves messages and never payments, and the Collections screen.

**Plan.** Milestone 4, collections: arrears buckets and the collections policy.

---

## Chunk 15 — the drafting agent ✅

_Date: 2026-10-12_

**What & why.** _Metaphor: the clerk who writes the letters, and the rule book on the desk beside
them._ The clerk is handed a folder for each late loan: who, how much, how late, what was said
before, and what was promised. They write one letter per folder. Before the letter goes in the
manager's tray, someone holds it against the rule book. A letter that breaks a rule goes back once,
with the broken rules circled. If it still breaks one, it goes in the tray anyway, flagged, so the
manager sees what went wrong rather than nothing at all.

Chunk 14 wrote the rule book and the morning list. This chunk is the clerk:

- **The folder is Repaydesk's work, not the model's.** `collections.drafter.gather` reads each
  loan live: the arrears and days late from Fineract, the borrower's first name from the
  directory, the last three messages, and the latest promise. A loan paid since the directory's
  copy, or now over 90 days late, is skipped without asking the model anything.
- **One call per loan, no tools.** Claude Sonnet 5.5 (`DRAFTER_MODEL`), low effort, with the policy
  as its system prompt and a fixed answer shape: the message, and a one-line note to the manager on
  why it's worded that way. The channel follows the bucket: WhatsApp for arrangement (room to talk
  about a plan), SMS for the rest.
- **Every draft goes through the policy's 11 checks.** A draft that fails is sent back once, with
  each failed check and its detail. Whatever comes back is saved as a draft with its checks.
- **A run per loan** in `agent_runs`, agent `collector`, outcome `drafted` or `failed` (migration
  010 adds `drafted`), with the brief it was given, the note, the attempts, cost and trace. The
  draft is in `collection_messages`, linked to its run, and the audit log has `message.drafted`.
- **Agent runs shows both agents.** The list has a "drafted a message" filter, and a collector
  row shows the amount due, the loan, the message and its status. A collector run's page shows
  what it was told, the message with its note to the manager, the policy checks, and where the
  message stands.
- **`python scripts/dev.py draft`** (`--limit N`, `--bucket B`), a compose `draft` service that
  shares the investigator's settings, and `make draft`.

**Found on the way.** *A run could end before it began.* `agent_runs.finished_at` was the
database's `now()`, which is the start of the transaction and comes from the database's clock;
`started_at` came from the application's. A full test run caught a run of -0.0002 seconds. Both
times now come from the application, for the investigator and the drafter. (Chunk 13's section
is corrected to say so.) Whether this explains chunk 14's unreproduced failures I can't say: they
weren't captured.

**Decisions worth recording.**

- *No tools for the drafter.* Everything it needs fits in a short brief, and Repaydesk can gather
  it more reliably than a model can look it up. Fewer moving parts, a cheaper call, and nothing to
  look up wrongly.
- *One repair, not a loop.* A second failure says the case needs a person, not a third try.
- *A failing draft is kept, not dropped.* The manager sees the loan was attempted and why it
  failed; a silent gap on the list would look like nothing was due. Chunk 16's approval refuses a
  draft that fails a check until a person edits it.
- *The amount is the live arrears*, not the directory's copy, so a part-payment since the morning
  copy is reflected in the message.

**Principles.** _Same clerk-and-rule-book metaphor._ *Hand the clerk the folder; don't send them to
the archive.* *Every letter is held against the rule book.* *One correction, then a person.* *A
flagged letter in the tray beats a missing one.*

**Files.**

- New:
  - `src/repaydesk/collections/drafter.py`, `collections/draft.py`;
  - `migrations/010_drafter.sql`;
  - `tests/test_drafter.py`.
- Changed:
  - `investigator.py` (the end time from the same clock);
  - `views.py` (runs about a loan: the message, the loan, the agent);
  - `tests/test_approvals.py`;
  - `web/src/api.ts`, `pages/Runs.tsx`, `pages/Runs.test.tsx`;
  - `compose.yaml`, `scripts/dev.py`, `Makefile`, `README.md`.

**Depends on / blocks.** Depends on the policy, the morning list and the schema (chunk 14), and on
the investigator's usage and cost accounting (chunk 11). Blocks chunk 16, where the manager
approves, edits or rejects the drafts and the gateway sends them.

**Verification.** `ruff` clean.

- `pytest`: 135 passed, 4 new. All four drafter tests use a scripted model behind a mock transport:
  no network, no key.
  - **A good draft** is saved as a draft by `agent:collector` with the live amount due, the
    bucket's channel and 11 passing checks. Its run is a `collector` run about the loan, with no
    payment line. The request had no tools, and the brief named the loan, the first name and the
    amount. The audit has `message.drafted`, and the loan leaves the morning list.
  - **A draft that threatens the police** is sent back once. The repair request carries the
    failed `no_threats` check with its detail, and the second draft passes.
  - **A draft with the wrong amount twice** is saved with exactly one failed check, `amount_due`.
  - **A paid loan** is skipped, and the model is never asked.
- The gateway's tests: 30 passed with migration 010.
- `vitest` 32 passed, 1 new: a collector run's page shows what it was told, the message, "2
  drafts" with "repaired after failing a check", the policy result and the note to the manager,
  and no lookups section. The runs list shows the collector row first, with "message draft". `tsc`
  clean.
- **On the stack:** the API applied migration 010, and `docker compose config` resolves the
  `draft` service with the drafter's model and service name over the investigator's settings.

What it does not prove:

- **The drafter against the real model.** The API key's credit ran out in milestone 3, so no live
  drafts yet. What Sonnet writes, how often it passes first time and what a draft costs are
  unmeasured; the tests prove only the plumbing around the model.
- **That the messages read well to a Nigerian borrower.** That's the manager's eye, and the policy
  review, not a test.

**PR package.**

- Subject: `Chunk 15: the drafting agent: one message per late loan, checked against the policy`
- Description:
  > Adds the collections drafting agent: Claude Sonnet 5.5 drafts one message per loan on the
  > morning list from facts Repaydesk gathers live, with no tools. Each draft runs through the
  > policy's checks, gets one repair if it fails, and is saved as a draft for the portfolio manager
  > with its checks; nothing is sent. Runs are recorded as `collector` runs and shown on Agent runs.
  > Also takes a run's end time from the same clock as its start.

**Plan.** Milestone 4, collections: the drafting agent under the written policy.

---

## Chunk 16 — approve and send ✅

_Date: 2026-10-14_

**What & why.** _Metaphor: the manager's signature, and the post room._ A letter the clerk wrote
goes nowhere until the manager signs it. Before signing, the manager checks it against the loan as
it is that morning, not as it was when the letter was written. A signed letter goes to the post
room, which only sends during the day. Before sending, the post room checks the signature is one it
recognises and that nothing has happened since (a phone call where the borrower promised to pay,
say) that makes the letter wrong to send.

Chunk 15 wrote the letters. This chunk adds the signature and the post room:

- **The manager's decision**, `collections.decide` and the Messages screen (`/messages`):
  - **Approve** as drafted, or **edit and approve**. Either way the message is checked again
    against facts read live then: the arrears from Fineract, the messages this week, any promise.
    A message that fails is refused with the failed checks, so the manager can fix it. A draft
    that failed at drafting can't be approved as it stands. The drafter's words are kept when a
    person edits them.
  - **Reject**, with a reason. The loan goes back on the morning list for a new draft.
  - Only portfolio managers and supervisors decide. Officers and auditors can read.
  - An approved message gets the borrower's number from the directory, a key (`CM-<id>`) and its
    first moment inside contact hours. It counts as waiting, so the loan stays off the morning
    list until it is sent.
- **The gateway sends** (`Messages.cs`), the same way it posts:
  - It locks one approved message at a time and records the result with an audit row in the same
    transaction.
  - It wakes on `NOTIFY message_outbox` (migration 011's trigger) or the poll.
  - Outside 08:00 to 18:00 Lagos time, Monday to Saturday, it holds the message until the next
    window. A retry never goes out at night either.
  - Before sending it checks again: the approver's role, the policy checks, two sent this week,
    and a promise to pay made since approval. Any of these fails the message for a person.
  - A provider outage (5xx, a timeout, our token refused) is retried with the postings' backoff.
    A refusal of the message itself (a number that can't receive) fails it for a person.
- **A messaging simulator** (`repaydesk.simulators.messaging`, compose `messaging`,
  http://localhost:18083): an aggregator-shaped API that dedupes on the client reference, refuses
  a malformed or unreachable number, and keeps each phone's inbox on a page. Nothing leaves the
  machine.
- **Navigation:** Messages, with the number of drafts waiting.

**Decisions worth recording.**

- *Check at approval and again at sending.* Approval checks what the words say against the loan.
  Sending checks what may have happened since. Both can change between a draft and a send.
- *An edit is checked like a draft.* A manager can fix a message, not bypass the policy.
- *The API never talks to borrowers.* Approval only marks the message; the gateway, the one
  component allowed to act outside (ADR 0002), sends it.
- *Contact hours in the gateway as a fixed UTC+1.* Lagos has no daylight saving, so the gateway
  needs no time-zone database. The Python side uses the zone by name; both are tested against
  the same boundaries.
- *The simulator's store is a SQLite file in a named volume*, so a restart doesn't make it forget
  which keys it has seen.

**Principles.** _Same signature-and-post-room metaphor._ *No letter leaves unsigned.* *Sign
against today's facts.* *The post room checks again before it sends.* *Nothing is posted at
night.*

**Files.**

- New:
  - `src/repaydesk/collections/decide.py`, `simulators/` (`__init__.py`, `messaging.py`);
  - `migrations/011_message_outbox.sql`;
  - `tests/test_messages.py`, `tests/test_simulator.py`;
  - `gateway/src/Repaydesk.Gateway/Messages.cs`, `gateway/tests/.../MessagesTests.cs`;
  - `web/src/pages/Messages.tsx`, `Messages.test.tsx`.
- Changed:
  - `api.py` (the message routes), `views.py` (drafts waiting in the summary), `collections/queue.py`
    (an approved message is waiting too);
  - `tests/test_collections.py` (sent rows carry a phone, a send time and a key);
  - the gateway's `Program.cs`, `Drainer.cs` (both outboxes), `Settings.cs`, `Outbox.cs`
    (`Sent`, `Deferred`), `README.md`, `tests/.../TestDatabase.cs`;
  - `web/src/api.ts`, `App.tsx`, `components/Shell.tsx`, `pages/Collections.tsx`, `pages/Review.tsx`
    (the reject form, shared), `styles.css`, `test-setup.ts`, `vite.config.ts`, and three tests'
    summary stubs;
  - `services/core/Dockerfile` (a store for the simulator), `compose.yaml`, `scripts/dev.py`,
    `README.md`.

**Depends on / blocks.** Depends on the drafts (chunk 15), the policy (chunk 14) and the gateway
(chunk 8). Blocks chunk 17: replies to these messages, and promises to pay from them.

**Verification.** `ruff` clean.

- `pytest`: 145 passed, 10 new.
  - **Approval:** a manager's approval sets the key, the borrower's number and Tuesday 08:00 for
    an approval on Monday at 19:30; the loan stays off the list. An edit with "your family" is
    refused, and a fixed one approved by a supervisor keeps the drafter's words. A draft that
    failed can't be approved until fixed. A part-payment since drafting makes the old amount fail.
  - **Who:** officers and auditors are refused, a rejection needs a reason, a rejected message
    can't then be approved, and the loan comes back on the list.
  - **Over HTTP:** 503 without core banking, 403, 422 with the failed checks, 409 on a second
    decision, 404 for no such message.
  - **The simulator:** one message however often it's sent, the refusals, and its page escapes
    what it shows.
- The gateway: 48 passed, 18 new. It sends once and records it. A Saturday 19:30 approval is held to
  Monday 08:00, not sent on Sunday. An outage is retried and still sends once. A refused number
  fails. An officer's approval, a failed check, a promise since and a third message in a week are
  each refused. The contact-hours boundaries and the HTTP client's classification are tested too.
- `vitest`: 36 passed, 4 new. An edit refused by the policy shows why on the card; the fixed one is
  approved. A failed draft's Approve is disabled. A rejection sends its reason. An officer reads
  and can't decide, and the Sent tab shows the provider's id and the drafter's original words.
  `tsc` clean.
- **On the stack, end to end:** one hand-written draft (no model call) for the first loan due,
  LN-2026-001565, approved as Ngozi through `decide.approve` against live Fineract at 17:27 Lagos
  time. The gateway sent it by WhatsApp. Its first three attempts got 500s from the simulator
  (below), it backed off, and the fourth was accepted as `SIM-000001`. The audit has
  `message.drafted`, `message.approved`, three `message.retry_scheduled` and `message.sent`.

**Found on the way.** *The simulator couldn't write its store.* The named volume mounted at `/sim`
was owned by root and the image runs as a non-root user. The image now creates `/sim` for that
user. The gateway's retries carried the message through the outage, which was a useful
unplanned test. *The web tests timed out on a cold run* as the suite grew: the first test in a file
pays for Vite transforming the pages. The per-test limit is now 15 seconds and `findBy` waits 3.

What it does not prove:

- **A real SMS or WhatsApp provider.** The simulator stands in for one. WhatsApp's template
  rules for business-initiated messages, delivery receipts and opt-outs aren't modelled.
- **The Messages screen live in a browser.** It's covered by its tests; the live check went
  through `decide.approve` directly.
- **Drafts from the real model.** API credit is still out.

**PR package.**

- Subject: `Chunk 16: approve and send: the manager's decision, the gateway sends inside contact hours`
- Description:
  > Adds the portfolio manager's decision on drafted collections messages (approve, edit and
  > approve, reject), with the policy checked again against the loan at approval, and the Messages
  > screen. The gateway sends approved messages to an SMS/WhatsApp simulator inside contact hours,
  > rechecking the approver, the weekly limit and any promise made since; outages are retried and
  > nothing is sent twice.

**Plan.** Milestone 4, collections: SMS/WhatsApp simulators; a manager approves the drafts.

---

## Chunk 17 — replies and promises to pay ✅

_Date: 2026-10-14_

**What & why.** _Metaphor: the clerk who opens the post that comes back._ Some replies say exactly
what's needed: "I go pay Friday." The clerk writes that in the promise book against the loan and
puts a note on the file not to chase before Friday. Others don't: "I don pay" (check for the money),
"how much is left?" (ring them), "stop" (someone senior decides). Those go in the manager's tray.
The clerk never guesses a promise the borrower didn't make, because the bank will hold them to it.

Chunk 16 sent the messages. This chunk takes the answers:

- **The simulator answers back.** Each phone's conversation on the simulator's page has a box to
  reply as the borrower; `POST /v1/replies` does the same for scripts. The reply goes to
  Repaydesk as a provider's webhook would: `POST /webhooks/messaging`, signed with HMAC-SHA256,
  with an id that makes a redelivery the same reply.
- **Repaydesk stores it once and reads it**, `collections.replies`:
  - it works out who replied (the directory's phone) and what they're answering (the last message
    sent to that number, so the loan and the amount asked for);
  - **the reader is rules, not a model**, in English and Nigerian Pidgin. Dates: today, tomorrow,
    "next tomorrow", weekdays and "next Friday", "end of the month", 20/10, "15th oct", "25th".
    Amounts: ₦30,000, NGN, naira, "20k".
  - **a promise** needs a date from today up to a month away and a paying word, or a reply that is
    little more than the date ("Friday"). It's recorded on the loan for the amount the reply names,
    or else the amount the message asked for, replacing any earlier open promise. The morning list
    already leaves a loan alone until its promise date (chunk 14), and the gateway won't send an
    approved message after a promise (chunk 16).
  - **everything else waits for a person**: says_paid ("I don pay"), question, stop, unclear (no
    date; a date but not paying; a past date; more than a month away; two amounts; "paid" and a
    date), and unknown_number.
- **The Replies tab** on the Messages screen: replies waiting for a person, with what they
  answered and how they were read. A manager or supervisor records the promise themselves (a date
  up to a month away, an amount, on that borrower's loan) or closes the reply with a note. Replies
  read as promises show the promise and that the rules recorded it.
- **Navigation:** Messages counts drafts and replies waiting.
- Migration 012: what a reply answers, how it was read, who dealt with it.

**Decisions worth recording.**

- *Rules, not a model, for promises.* A promise is something the bank will hold a borrower to, so
  it should come from words a person can point to. The rules say why they read each reply as they
  did (`reading.why`), and anything they aren't sure of goes to a person. A model could take the
  unclear replies later, as a suggestion for the manager, not a recording.
- *Read in the request.* The webhook stores the reply, then reads it in the same request. Unlike
  matching a payment, reading a reply is quick and needs no lookups outside Repaydesk's database.
- *One open promise per loan.* A later promise replaces the earlier one (cancelled, with a note),
  so "not before the promised date" has one date.
- *"Stop" is a person's decision.* The prototype has no opt-out register yet; a stop waits on the
  Replies tab and the manager can reject drafts for that borrower.

**Principles.** _Same returned-post metaphor._ *Record only the promise the borrower made.* *Say
why each reply was read the way it was.* *What the rules can't read, a person does.* *One date
to hold them to.*

**Files.**

- New:
  - `src/repaydesk/collections/replies.py`;
  - `migrations/012_replies.sql`;
  - `tests/test_replies.py`.
- Changed:
  - `simulators/messaging.py` (replies, the conversation page, signed delivery);
  - `adapters/webhooks.py` (`MessagingWebhooks`), `api.py` (the webhook and the reply routes),
    `views.py` (replies waiting in the summary);
  - `web/src/api.ts`, `components/Shell.tsx`, `pages/Messages.tsx`, `pages/Messages.test.tsx`, and
    three tests' summary stubs;
  - `compose.yaml`, `scripts/dev.py`, `README.md`.

**Depends on / blocks.** Depends on sending (chunk 16) and the promises table (chunk 14). Blocks
chunk 18: a promise kept or broken by what posts by its date, a broken one back on the list, the
end-to-end demo, and the m4 tag.

**Verification.** `ruff` clean.

- `pytest`: 171 passed, 26 new, on a clean full run (4 min 48 s).
  - **The reader:** 21 replies, each with what it should become, including Pidgin ("I go pay
    Friday", "next tomorrow I go send am", "I don pay"), "I will call you on Friday" (a date, not
    paying), a past date, more than a month away, two amounts, and "paid" with a date. A day and
    month in December naming 5 January means next year.
  - **Recording:** "I go pay Friday" becomes an open promise on the loan messaged, for the amount
    asked, recorded by `rule:replies`; the loan leaves the morning list. A redelivery is the same
    reply. A later promise with an amount replaces the first.
  - **People:** says_paid and an unknown number wait; an officer can't close; a note is required;
    a second close is refused. A person's promise is refused for a past date, more than a month
    away, no amount, or another borrower's loan, then recorded on the reply's loan.
  - **End to end in-process:** a forged signature is 401, an unreadable event 400. A reply typed in
    the simulator's form is delivered, signed, to the API and becomes a promise. Redelivering it
    keeps one promise. A question through `/v1/replies` arrives on the channel last used, waits,
    and is closed over HTTP; a second close is 409.
- The gateway: 48 passed with migration 012.
- `vitest`: 38 passed, 2 new. A manager's promise for a date too far shows the refusal on the
  card, then a fixed one is recorded with the amount typed. A reply is closed with a note. A
  promise read by the rules shows as recorded from the reply, and an officer can only read.
  `tsc` clean.
- **On the stack:** the API applied migration 012. A reply "I go pay Friday" through the
  simulator's API from the phone that got chunk 16's live message was delivered to the API
  (`delivered: promise`). LN-2026-001565 has an open promise for 16 Oct for the ₦726,000 asked,
  recorded by `rule:replies`.

What it does not prove:

- **How well the rules read real borrowers.** The replies tested are ones I wrote. Real replies
  will be messier: misspellings, voice notes, other languages. The unclear tray is the safety net.
- **The Replies tab live in a browser.** It's covered by its tests.
- **Opt-outs.** "Stop" waits for a person; nothing stops future drafts automatically.
- **The flaky full runs.** An earlier full run of the same code had 8 failures, two of them named
  in `test_records.py` (the directory sync tests). I didn't capture the tracebacks. That file
  passes alone, and the next full run passed whole. The failing run took 13 minutes against under
  5 for the clean one, so load is the likely link, not a cause. This is the third sighting (chunk
  14 had 7, chunk 15 one, which was the clock fix). Next time it goes to a logged run straight
  away.

**PR package.**

- Subject: `Chunk 17: replies and promises to pay: read by rules, the rest to a person`
- Description:
  > The messaging simulator now sends borrowers' replies back as signed webhooks. Repaydesk stores
  > each once, works out the loan it answers, and reads it by rules in English and Pidgin: a reply
  > with a date and an intent to pay becomes a promise to pay on that loan; anything else waits on
  > the Messages screen's Replies tab, where a manager records the promise or closes the reply.

**Plan.** Milestone 4, collections: a reply with a date becomes a promise to pay.

---

## Chunk 18 — promises kept and broken, and the milestone 4 demo ✅

_Date: 2026-10-15_

**What & why.** _Metaphor: the promise book, checked against the till._ Every morning the clerk
runs a finger down the promise book. Where the money has come in, the promise is ticked as kept.
Where the date has passed and it hasn't, the promise is crossed out, and the loan goes to the top
of the day's list, marked "promised and didn't pay", until someone has written to the borrower
about it. Then it's an ordinary late loan again.

Chunk 17 wrote promises into the book. This chunk checks them, and runs milestone 4 end to end:

- **Settling promises**, `collections.promises.settle`:
  - **kept** once repayments of at least the promised amount have posted to the loan between the
    day it was made and the promised date. Read live from Fineract, so a branch payment, a transfer
    or a Repaydesk posting all count, and it's settled as soon as the money is there.
  - **broken** the day after the promised date, if not: the borrower has all of the promised day.
  - each with a note ("₦5,000.00 of ₦20,000.00 repaid by 08 Oct") and an audit row from
    `service:promises`.
  - The directory service runs it after each sync, every 15 minutes, with the core banking reads
    it already has; `python -m repaydesk.collections.promises` runs it by hand.
- **The morning list**, corrected:
  - a broken promise brings the loan back first, as a broken promise, **until a message is
    drafted about it**. Before, it stayed "broken" for good.
  - the loan's **latest** promise decides, so a newer kept promise ends an older broken one.
    Before, a kept promise was ignored and the older broken one still showed.
- **The milestone 4 demo**, `python scripts/dev.py demo-m4` (`repaydesk.collections.demo`), against
  the running stack:
  1. two loans from the morning list, from borrowers with a phone;
  2. drafted, from a fixed template by default (`--model` uses the drafting agent), through the
     policy's checks;
  3. approved by Ngozi through the API;
  4. sent by the gateway to the simulator and found in each phone's inbox. Outside contact hours
     it stops here and says when they'll go;
  5. each borrower replies through the simulator ("I go pay tomorrow", "I go pay today"), and
     each reply becomes a promise;
  6. one pays: a statement line, matched by rule, approved by Amaka (and Tunde if needed), posted
     by the gateway to Fineract and Odoo; the promise is checked and is kept;
  7. the other doesn't: checked as of tomorrow, the promise is broken (the note and the audit say
     "as of"), and the loan is at the top of the morning list as a broken promise.
- **CI** runs it after the milestone 2 demo.

**Found on the way.** *The morning list sorted "promise lapsed" ahead of "no promise".* The order
put broken promises first with `(pr.status = 'broken') DESC NULLS LAST`. For a loan whose promise
had lapsed but wasn't settled yet, that's false, not NULL, and `DESC` puts false ahead of the NULLs
of loans with no promise at all. So those loans went to the top. The broken-promise test caught
it; the flag is now never NULL.

**Decisions worth recording.**

- *Kept by what core banking shows, not by what Repaydesk posted.* Borrowers pay at branches and
  by transfer as well as through matched statement lines; Fineract sees all of it.
- *Settled with the directory sync.* Same reads, same rhythm, no new service. A promise is at most
  15 minutes late to be marked.
- *The demo checks a broken promise "as of tomorrow".* It can't wait a day. It says so on the
  promise and in the audit, and only touches its own loans (`settle(..., loans=[...])`).
- *A template for the demo's drafts by default.* API credit is out, and the demo should run
  without a key. The drafts still go through the policy's checks and are marked
  `demo:template`, not the agent.

**Principles.** _Same promise-book metaphor._ *Tick a promise when the money is in the till.*
*Give them all of the promised day.* *A broken promise goes to the top once, not forever.*

**Files.**

- New:
  - `src/repaydesk/collections/promises.py`, `collections/demo.py`;
  - `tests/test_promises.py`.
- Changed:
  - `collections/queue.py` (broken until followed up; the latest promise decides; the sort);
  - `directory.py` (settles promises after each sync);
  - `compose.yaml` (the demo service: messaging and model settings), `scripts/dev.py`
    (`demo-m4`), `Makefile`, `.github/workflows/ci.yml`, `README.md`.

**Depends on / blocks.** Depends on chunks 14 to 17. Completes milestone 4.

**Verification.** `ruff` clean.

- `pytest`: 175 passed, 4 new.
  - **Kept and broken:** two payments that add up keep a promise before its date. A short payment
    leaves it open on the promised day and breaks it the day after. A payment made before the
    promise doesn't count. Settling again changes nothing. Audit: one kept, two broken.
  - **As of:** checking as of another day says so on the promise and in the audit.
  - **The list:** a promised loan is left alone; once broken it's first, as a broken promise.
    After a follow-up is sent it's back in its own bucket, not first, and the broken-promise filter
    is empty. A newer kept promise ends an older broken one.
- The gateway: 48 passed.
- **On the stack, outside contact hours** (00:15 Lagos, 15 Oct): the demo took LN-2026-001120 and
  LN-2026-000151 from a list of 936, drafted both from the template (11 of 11 checks each), Ngozi
  approved both through the API, and it stopped at step 4: held until Thu 15 Oct 08:00.

What it does not prove:

- **Steps 4 to 7 of the demo live.** They need contact hours. Each part has been checked on its
  own (sending in chunk 16, a reply becoming a promise in chunk 17, posting in milestone 2,
  settling in the tests above), but not the demo running through them. Milestone 4 is tagged only
  once it has: see below.
- **The directory service settling promises live.** It's the same call the tests make, after a
  sync; I haven't watched it settle one on the stack.

**PR package.**

- Subject: `Chunk 18: promises kept and broken, and the milestone 4 demo`
- Description:
  > Settles promises to pay against the loan's repayments in Fineract: kept once the promised
  > amount has been repaid, broken the day after its date. Runs after each directory sync. A
  > broken promise puts the loan at the top of the morning list until a follow-up is drafted; the
  > latest promise decides. Adds the milestone 4 demo (`python scripts/dev.py demo-m4`) and runs it
  > in CI.

**Plan.** Milestone 4, collections: done when the demo passes in contact hours, then tag `m4`.

---
