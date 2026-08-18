# Phase 1E Controlled Production Rollout Gate

Status as of 2026-08-12. This is an evidence record and operator plan. It does not
authorize a push, merge, deployment, database write, secret change, or Sync.

Production credential propagation, trusted-TLS classification, the duplicate audit,
backup/restore evidence, and Storefront-token classification were continued on
2026-08-13. The authoritative continuation is
`docs/PHASE_1E_1_RELEASE_UNBLOCK.md`; this file preserves the earlier gate as historical
evidence.

## A. Git ancestry — VERIFIED

`v2/export-provenance-ui` is linear from `main` and contains Phase 0 through Phase 1D:

```text
f346f70 main / origin/main / archive baseline
  07f780b Phase 0
  27017ab Phase 1A
  68db83e Phase 1B.1
  5f64a28 Phase 1B.2
  c7c6f32 Phase 1B.2 release gate
  e70de6d Phase 1C
  b25c783 Phase 1D
```

The exact common ancestor of `main` and `b25c783` is
`f346f7046b223b579c9ff3769f358c71e98f8989`; `b25c783` is seven commits ahead.
The clean integration shape is one reviewed cumulative PR into `main`. Do not create
overlapping phase PRs, cherry-pick, rewrite, merge, or push.

## B. Local release verification — VERIFIED

The release-equivalent suite runs against disposable PostgreSQL 16. No test performs a
live storefront request.

| Gate | Result |
|---|---|
| Full backend | 215 passed; 11 documented warnings |
| Critical workflows | 142 passed; 73 deselected |
| Durable Sync lifecycle | 34 passed |
| Product identity/concurrent reconciliation | 37 passed |
| Search + shared cycle policy | 39 passed |
| Export + shared cycle policy | 28 passed |
| Schema authority | 12 passed |
| Workflow/security assertions | 4 passed |
| Frontend behavior | 16 passed |
| TypeScript | pass |
| Production frontend build | pass; 2,414 modules, 710.51 kB main chunk |
| Fresh database | Case D -> migrations 0001..0005 -> Case A |
| Migration round trip | head -> base -> head passed |
| Managed 0003 upgrade | Case A- -> 0004 -> 0005 -> Case A |
| Unstamped 0003 recovery | Case B- -> stamp exactly 0003 -> A- -> 0004/0005 |
| Alembic drift | `No new upgrade operations detected` |
| Strict startup smoke | 36 routes |
| Current HEAD / release diff secret scan | no findings |
| Full Git history secret scan | blocked by one pre-existing third-party token in commit `9660f6b` |

Release verification found and fixed three blockers with regression coverage:

1. the classifier's B- fingerprint had not been updated for 0005 and misclassified a real
   unstamped 0003 schema as Case C;
2. the classifier printed `DATABASE_URL` on connection failure;
3. automatic V2 work had no Stage A kill switch across the Vercel compatibility cron and
   GitHub schedule.

`SYNC_MORNING_ENABLED` is now default-off at the Vercel cron route, worker CLI, and GitHub
scheduled job. Manual request-id processing remains available.

## C. Production configuration — BLOCKED

Only names/metadata were inspected; no value is recorded here.

### Vercel/application

| Setting | Status |
|---|---|
| Current production deployment | present, but still `main@f346f70` (pre-V2) |
| `DATABASE_URL` | present |
| `DB_SCHEMA_CHECK` | absent; future V2 default would be `warn` |
| `SYNC_EXECUTION_MODE` | absent; future V2 default would be `v2` |
| `SYNC_DISPATCH_PROVIDER` | absent; future V2 default would be `none` |
| `SYNC_MORNING_ENABLED` | absent; future V2 default is disabled |
| `GITHUB_ACTIONS_DISPATCH_TOKEN` | absent |
| `GITHUB_ACTIONS_REPOSITORY` | absent |
| `GITHUB_ACTIONS_WORKFLOW` / `GITHUB_ACTIONS_REF` | absent; safe code defaults exist |
| `CRON_SECRET` | absent |
| legacy `RUN_SCANS_INLINE` | present, value not inspected |
| existing Discord fallback credential | present, value not inspected |
| Redis/Celery production settings | absent |

The deployment is publicly reachable and the application has no authentication. That is
an owner decision recorded in `SECURITY.md`, but it becomes a rollout risk because V2 Sync
request endpoints write durable work. Before enabling server dispatch or morning drains,
use Vercel deployment protection/authenticating proxy or explicitly accept the public
trigger risk. At minimum, configure `CRON_SECRET` so compatibility cron routes fail closed
when called without the bearer value.

### GitHub

| Setting | Status |
|---|---|
| Workflow files on default `main` | absent (GitHub reports zero workflows) |
| `production-sync` environment | absent |
| Environment `PRODUCTION_DATABASE_URL` | absent |
| Repository Actions `SYNC_MORNING_ENABLED` variable | absent (safe/default-off) |
| Environment branch restriction to `main` | absent |
| Required reviewer | absent; appropriate for eventual unattended mornings |
| Repository secrets | none |
| `main` branch protection | absent |
| Local workflow permissions | verified `contents: read` only |
| Trusted checkout/actions | verified pinned SHAs and no persisted checkout credentials |

The manual migration workflow now uses the same `production-sync` environment as Sync,
refuses non-`main` refs, checks out trusted `main`, and retains its read-only default plus
literal `MIGRATE` confirmation for writes.

## D. Production schema classification — BLOCKED

No case was established. The first attempt stopped before connection because Vercel masks
a sensitive boolean in an environment pull. The second reached TLS setup but failed local
certificate verification before a catalog query. Its error path exposed the database URL;
that defect is fixed locally, but the existing production database credential must be
rotated before another connection attempt.

After rotation, use Python 3.12 in a trusted environment with a valid CA bundle:

```bash
cd backend
SSL_CERT_FILE="$(.venv/bin/python -c 'import certifi; print(certifi.where())')" \
DATABASE_URL="<rotated-production-async-url>" \
.venv/bin/python scripts/check_schema_state.py
```

Do not stamp or migrate from this command. Record exactly one of A, A-, B, B-, C, or D.

## E. Production duplicate audit — BLOCKED

Not run because schema classification did not complete and the production credential must
be rotated first. After a successful classification at a schema where the audit is valid:

```bash
cd backend
DATABASE_URL="<rotated-production-async-url>" \
.venv/bin/python scripts/audit_product_duplicates.py --json
```

Retain only the product count and summary group/affected-row counts in release evidence.
If logical groups or invalid identities exist, stop before 0004 and make an explicit
remediation decision.

## F. Backup/recovery — BLOCKED

Repository backup automation does not exist. Provider metadata suggests the database is
managed externally, but the actual backup mechanism, newest recoverable timestamp,
recovery target, restore steps, retention, and prior restore-test result were not
inspectable. A migration is blocked until all six are recorded.

Minimum independent pre-write backup command:

```bash
pg_dump --format=custom --file "/secure/outside-repo/market-monitor-pre-v2.dump" \
  "$DATABASE_URL"
```

Restore validation must target a separate empty database, never production:

```bash
pg_restore --no-owner --dbname "$RESTORE_DATABASE_URL" \
  "/secure/outside-repo/market-monitor-pre-v2.dump"
```

Record the UTC backup timestamp, target database, successful restore completion, and a
read-only row/schema sanity check before authorizing a production write.

## G. Migration plan — READY BUT NOT EXECUTED

The selected path remains blocked on D–F. First pause every scan producer/worker, verify no
real lease owner is active, take and restore-test the backup, then classify.

- **A:** no schema action. Verify head is `0005_durable_sync_lifecycle` and inspect the
  required constraints/indexes.
- **A-:** use `alembic current`. Upgrade to 0003 first if older; run the duplicate audit;
  upgrade to 0004 only with zero blocking groups; re-audit; inspect overlapping legacy
  `running` rows; then upgrade to 0005.
- **B:** only after structural output shows no missing model tables/columns, backup, and
  explicit authorization: `alembic stamp head`; then reclassify A and audit.
- **B-:** only after backup and explicit authorization:
  `alembic stamp 0003_reconcile_index_names`; require A-; audit; upgrade 0004; re-audit;
  inspect legacy running rows; upgrade 0005.
- **C:** stop. Save sanitized `--json` drift evidence and reconcile deliberately. Never
  stamp or run `upgrade head`.
- **D:** only if genuinely empty, backup/recovery expectations are satisfied, and explicit
  authorization is given: `alembic upgrade head`.

For any path through 0005, first run the exact overlapping-row query in `RUNBOOK.md` §2.4.
Afterward require `alembic current`, Case A, `sync_requests`, `sync_request_runs`, lineage
FKs, lifecycle checks, `uq_scrape_runs_competitor_non_terminal`, and
`ix_scrape_runs_claimable`.

## H. PR/main integration — READY BUT NOT EXECUTED

Use one reviewed PR from the cumulative branch into `main`; require green CI and review
all three workflows. Do not merge automatically. No branch was pushed and no PR was
created. After merge, verify the workflow files are visible on `main` before attempting
manual migration or Sync.

## I. Feature-flag sequence — READY BUT NOT EXECUTED

| Stage | Vercel/application | GitHub `production-sync` |
|---|---|---|
| A: code present, no automatic V2 | `SYNC_EXECUTION_MODE=v2`; `SYNC_DISPATCH_PROVIDER=none`; `SYNC_MORNING_ENABLED=false`; `DB_SCHEMA_CHECK=warn` | database secret present only after migration; repository Actions variable absent/false |
| B: one explicit smoke | same as A; create one request explicitly | manually dispatch its request UUID; morning false |
| C: manual V2 enabled | after proof, `DB_SCHEMA_CHECK=strict`; dispatcher may remain `none` or become `github_actions` with server-only token | morning false |
| D: automatic morning | leave Vercel morning false so its compatibility cron does not duplicate ownership | set repository Actions variable `SYNC_MORNING_ENABLED=true`; schedules at 07:17 and 08:47 Cairo become active |

Never run a V2 claim and a legacy scan for the same intended request. Keep legacy Celery
scan scheduling disabled while `SYNC_EXECUTION_MODE=v2`.

## J. One-competitor smoke — NOT AUTHORIZED

Candidate: **TubbysTubby, competitor 10**. Read-only production metadata shows it is active,
uses the Salla adapter, last reported success, and has 145 stored products—the smallest
currently healthy/non-empty representative. Confirm its health again immediately before
the smoke and select one known product/collection; do not use Shopbloxs.

Before the POST, record product totals, active totals, canonical/identity duplicate counts,
miss counters, removal-event count, last complete/terminal evidence, and a known product.
Then create exactly one idempotent competitor request, manually dispatch that UUID, and
observe queued -> running -> terminal. Require non-zero observations, sensible
completeness, ordered lifecycle timestamps, run-linked product observations/history,
unchanged identity uniqueness, no unexpected removal or mass miss increment, and sanitized
runner logs. Record only request/run IDs and aggregate evidence.

## K. Search proof — NOT AUTHORIZED

After J succeeds, use the preselected known product in production `/search`. Verify the
expected competitor and current observed price, product and complete-coverage ages,
complete/partial state, reliable/degraded verdict, same-currency statistics, and that
existing comparisons remain intact. HTTP 200 alone is not evidence.

## L. Export proof — NOT AUTHORIZED

Using the same competitor and bounded collection:

1. prepare explicit Live primary-format export; require `source=live`, truthful
   completeness/count/cap/times, no cached fallback, expected rows, and an opening file;
2. prepare explicit Cached export; require `source=cached`, stored observation/run
   lineage, and visible stored-mode wording;
3. use a safe known-invalid local/test path for failure semantics; do not attack the real
   storefront. Require live failure to remain distinct from cached success.

Check CSV, JSON, and JSONL compatibility, with the primary daily format receiving full
manual file inspection.

## M. Sync All — NOT AUTHORIZED

Only after J–L pass. Capture per-competitor product totals/statuses and the global removal
event count. Submit one durable Sync All request, then record every run state, duration,
observed/new/updated/removal count, completeness and failure. Treat Shopbloxs zero as
`suspicious_empty`/failure evidence and require no mass-removal inference.

## N. Morning automation — BLOCKED

The code-level gate is ready and default-off. Enabling requires: workflows on `main`, the
protected environment and database secret, successful manual J–M proof, no required
reviewer, and an explicit owner change of the GitHub repository Actions variable to true.
Verify both Cairo schedules reuse `automatic:<date>` and the 08:47 recovery does not
duplicate a successful 07:17 run.

## O. Rollback — READY BUT NOT EXECUTED

Set the GitHub repository Actions variable `SYNC_MORNING_ENABLED=false`, disable server
dispatch, stop active V2 runners, and inspect leases before legacy work. If needed, deploy
`SYNC_EXECUTION_MODE=legacy`. Preserve migration 0005 and durable history; do not
downgrade merely to switch behavior. Search continues from persisted products. Do not run
legacy and V2 for one intended scan.

Legacy rollback capability is incomplete unless its actual production worker/inline
execution and Redis configuration are confirmed; Vercel currently exposes no Redis/Celery
settings and the value of `RUN_SCANS_INLINE` was not inspected.

## P. Blockers

1. Rotate the production database credential exposed by the old classifier error path.
2. Re-run the production classifier with valid TLS trust and establish one exact case.
3. Run and sanitize the production duplicate audit.
4. Document and restore-test a real backup/recovery point.
5. Push/review/merge the one cumulative PR; workflows do not yet exist on `main`.
6. Create/restrict `production-sync`, add its database secret, leave morning false.
7. Decide how the public unauthenticated V2 request endpoints will be protected or accept
   that risk explicitly; configure `CRON_SECRET`.
8. Obtain separate authorization for database writes, deployment, one smoke Sync, Search/
   Export proof, Sync All, and morning enablement in that order.
9. Decide how to handle the pre-existing third-party Storefront token retained in public
   Git history; current HEAD and the Phase 1E diff scan clean, but the full-history gate is
   red and must not be suppressed silently.
