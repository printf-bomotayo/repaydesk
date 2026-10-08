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

**Principles.** _Same mandate metaphor._ *Check the mandate before paying, not after* — the rules run
inside the transaction that would act on them. *Two signatures, one with authority* — the second
pair of eyes needs a limit to back it, or it's a ritual. *The counterfoil is written with the
cheque* — approval and hand-off commit together or not at all. *Least privilege by role* — an auditor
can see every decision and make none.

**Files.** New: `services/core/src/repaydesk/approvals.py`, `staff.py`,
`migrations/004_approvals.sql`, `tests/test_approvals.py`, `docs/adr/0005-approval-rules.md`.
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
