# Treasury Audit — preliminary design

**Status: DESIGN ONLY. Nothing in this document is implemented, and nothing in it should
be implemented during Phase 0 or Phase 1.**

This is a forward-looking design for a future capability: monitoring **public blockchain
activity for company-controlled and company-related wallet addresses**, so that treasury
movements can be reconciled and anomalies surfaced for human review.

---

## 1. Purpose and scope

**What this is.** A read-only reconciliation and audit-support tool. It watches public
blockchain addresses that the business already knows about — its own treasury wallets, its
payout wallets, known vendor and exchange addresses — and builds a reviewable record of
what moved where.

**What this is not.** It is not a surveillance system, not an investigation tool aimed at
individuals, and not an automated accusation engine. It does not decide that anyone did
anything wrong. Every signal it produces is an input to a human review process.

**Why it is called Treasury Audit.** The name reflects the function: auditing the
movement of company treasury funds. Framing it around monitoring people would be both
inaccurate and a poor foundation for the design — the system observes *addresses and
transactions*, which are not the same thing as people.

### Hard constraints

These are not guidelines. A design that violates any of them is the wrong design.

1. **Never store seed phrases or private keys.** The system has no custody and no signing
   capability. There is no code path that accepts a key. If a key is ever pasted into a
   field, that is an incident.
2. **Monitoring is read-only.** Public chain data in; nothing on-chain out. No
   transactions are constructed, signed, or broadcast.
3. **An address is not a person.** An address is an identifier controlled by an unknown
   party. Any mapping from address to person is an *assertion made by a human operator*,
   stored as such, with attribution and a timestamp — never inferred by the system.
4. **Repeated transfers are evidence of a transaction pattern, not proof of ownership or
   wrongdoing.** The vocabulary in the UI, the database, and the code must reflect this.
   A signal is called a signal, not a finding.
5. **Every risk signal requires human review.** No automated escalation, no automated
   notification to third parties, no automated adverse action.
6. **Approved addresses are labelable and whitelistable.** Known vendors, exchanges,
   payroll providers, and the company's own wallets must be classifiable so that ordinary
   business activity does not generate perpetual noise.
7. **Legal, privacy, and workplace requirements are jurisdiction- and
   deployment-dependent.** See §7. This document does not and cannot provide legal advice.

---

## 2. Domain model (proposed)

```
WatchedWallet 1───n WalletSyncJob
WatchedWallet 1───n BlockchainTransaction
BlockchainTransaction 1───n TokenTransfer
AddressLabel  n───1 (address)          human-asserted, versioned
FundingAllocation n───1 WatchedWallet  a known outbound company disbursement
RiskSignal    n───1 BlockchainTransaction (or TokenTransfer)
AuditCase     1───n RiskSignal
```

### `WatchedWallet`
An address the business has a legitimate reason to observe.

`id · chain · address · label · purpose · watch_reason · added_by · added_at · active`

`purpose` distinguishes `TREASURY`, `PAYOUT`, `VENDOR`, `EXCHANGE`, `COUNTERPARTY`.
`watch_reason` is free text and **required** — recording why an address is being watched is
a governance control, not documentation.

### `BlockchainTransaction`
A normalized, chain-agnostic record of one on-chain transaction touching a watched address.

`id · chain · tx_hash · block_number · block_time · from_address · to_address ·
native_value · fee · status · direction · raw_provider_payload`

`raw_provider_payload` is retained so a normalization bug can be corrected by
re-processing rather than by re-fetching.

### `TokenTransfer`
Token movements within a transaction (ERC-20/721/1155, TRC-20, SPL). One transaction can
contain many.

`id · transaction_id · token_address · token_symbol · token_decimals · from_address ·
to_address · amount (Decimal) · usd_value_at_time · price_source`

`usd_value_at_time` must record its `price_source` and be treated as an estimate. Valuation
is a frequent source of false confidence.

### `AddressLabel`
A **human assertion** about an address. Deliberately separate from `WatchedWallet` so that
labels can be applied to counterparty addresses without implying they are watched.

`id · chain · address · label · label_type · confidence · asserted_by · asserted_at ·
source · superseded_by`

`label_type`: `COMPANY_OWNED`, `APPROVED_VENDOR`, `EXCHANGE_DEPOSIT`, `KNOWN_SERVICE`,
`UNKNOWN`. `confidence`: `CONFIRMED` (documented) / `REPORTED` (someone said so) /
`HEURISTIC` (matched a public list).

Labels are **append-only and versioned** via `superseded_by`. Who claimed what, and when,
must remain auditable — this is exactly the kind of assertion that gets challenged later.

### `FundingAllocation`
A disbursement the company knows it made: a payout, a vendor payment, a float sent to an
operational wallet. This is the anchor that makes "what happened to the money we sent"
answerable.

`id · watched_wallet_id · amount · asset · allocated_at · purpose · reference ·
recorded_by`

### `RiskSignal`
An observation that a pattern occurred. **Not** a finding, not an allegation.

`id · signal_type · severity · subject_transaction_id · subject_wallet_id · detected_at ·
window_start · window_end · evidence (JSON) · status · reviewed_by · reviewed_at ·
review_note`

`status`: `OPEN → UNDER_REVIEW → EXPLAINED | ACTIONED | FALSE_POSITIVE`. There is no
terminal state reachable without a human.

`evidence` holds the concrete facts that triggered the signal — amounts, counts, addresses,
the window — so a reviewer can evaluate the reasoning rather than trust a score.

### `AuditCase`
A human-opened container grouping related signals for review.

`id · title · status · opened_by · opened_at · closed_at · outcome_note · signals[]`

### `WalletSyncJob`
Reuses the `ScrapeRun` pattern from ADR 0003: a durable job record with a real state
machine, a cursor, and idempotent processing.

`id · watched_wallet_id · chain · status · cursor_block · cursor_tx_index · started_at ·
finished_at · transactions_ingested · error_message`

The cursor makes ingestion resumable and idempotent — re-running from a cursor must not
duplicate transactions. `UNIQUE (chain, tx_hash, log_index)` enforces that at the database
level, which is the lesson learned from ARCH A-7.

---

## 3. `BlockchainProvider` adapter abstraction

The point of this abstraction: **EVM, TRON, Bitcoin, and Solana have genuinely different
transaction models, and none of that difference may leak into the domain.** UTXO versus
account-based, log-based token transfers versus native ones, differing finality
assumptions, differing address formats.

```python
class BlockchainProvider(Protocol):
    chain: ClassVar[Chain]

    async def get_transactions(
        self, address: str, cursor: SyncCursor | None, limit: int
    ) -> ProviderPage: ...

    async def get_token_transfers(
        self, address: str, cursor: SyncCursor | None, limit: int
    ) -> ProviderPage: ...

    def normalize_address(self, address: str) -> str: ...
    def validate_address(self, address: str) -> bool: ...
```

```python
@dataclass(frozen=True)
class ProviderPage:
    transactions: list[NormalizedTransaction]
    next_cursor: SyncCursor | None
    provider_name: str
    fetched_at: datetime
```

Same principles as ADR 0004: the HTTP client is injected; every provider returns a
validated canonical type; a shared contract test suite runs against every provider with
committed fixtures; **no test makes a live RPC call**.

Planned providers: `EvmProvider` (Ethereum, Base, Arbitrum, Polygon — one implementation
parameterized by chain id), `TronProvider`, `BitcoinProvider`. Each wraps a public indexer
or node RPC.

Chain-specific concerns that must stay inside adapters: UTXO input/output aggregation into
a `from`/`to` view; EVM log decoding; TRON's address encoding; reorg handling and
confirmation depth; per-provider rate limits and pagination semantics.

---

## 4. Risk indicators to explore later

**Not a scoring model. Not thresholds to hardcode.** These are patterns a human might want
flagged, listed so the design can accommodate them. Every one of them has an innocent
explanation that is usually the correct one.

| Indicator | Pattern | Common innocent explanation |
|---|---|---|
| Destination concentration | A large share of outflow from one wallet reaches a single address over a window | A single vendor, exchange, or payroll provider |
| Repeated unknown recipient | Multiple transfers to an address with no `AddressLabel` | The label simply has not been recorded yet |
| Rapid forwarding | Funds leave a wallet shortly after a recorded `FundingAllocation` arrives | Normal operational float being deployed as intended |
| Unusually large first-time recipient | First transfer to an address is far above the historical median | A new, legitimately large vendor payment |
| Repeated transfers below a threshold | Several transfers just under an internal approval limit | Genuine invoicing cadence, or a limit set too low |
| Shared recipient across business wallets | Multiple company wallets pay the same address | A shared vendor — usually exactly what you would expect |
| Circular flow | Value returns to an originating address through intermediaries | Rebalancing, bridging, or an exchange round-trip |
| Unapproved asset or network | Activity on a chain or token outside policy | A bridge, an airdrop, or dust sent unsolicited |

Design requirements that follow:

- Every signal must carry its **evidence** and its **window**, so a reviewer evaluates
  facts rather than a score.
- Every signal type must be individually **disableable** and its parameters
  **configurable** per deployment. Hardcoded thresholds would repeat the mistake found in
  the existing codebase, where `MIN_PRICE_CHANGE_AMOUNT` is configurable in three places
  and honoured in none.
- Whitelisted addresses (`APPROVED_VENDOR`, `EXCHANGE_DEPOSIT`, `COMPANY_OWNED`) suppress
  signals by default, with the suppression itself recorded so it is auditable.
- Dust and airdrop transactions must be filterable — unsolicited inbound transfers are
  common and are not evidence of anything.
- **No automated notification of a risk signal to anyone other than the operator who
  configured the system.** No automated adverse action of any kind.

---

## 5. Architecture fit

Treasury Audit is a **module inside the existing modular monolith** (ADR 0001), not a
separate service. It reuses the patterns established in Phase 0/1:

```
api/treasury/                 routes; HTTP only
application/treasury/         SyncWatchedWallet · EvaluateRiskSignals · ManageAuditCase
domain/treasury/              entities above + pure signal evaluation
ports/                        BlockchainProvider · PriceOracle
infrastructure/blockchain/    evm · tron · bitcoin providers
workers/                      wallet sync + signal evaluation entrypoints
```

Reused directly:

- The `ScrapeRun` job pattern → `WalletSyncJob` (durable record, state machine, cursor).
- The adapter + canonical-type + fixture-contract-test pattern from ADR 0004.
- The transactional outbox from ADR 0006 for any operator-facing notification.
- Alembic-only schema ownership (ADR 0002) and real uniqueness constraints from the start.

Nothing in Treasury Audit is a reason to introduce microservices.

---

## 6. What must be built first

Treasury Audit **depends on** the Phase 1 foundations. Building it earlier would replicate
the defects Phase 0 identified.

Prerequisites: the adapter pattern and its contract-test harness (ADR 0004); the durable
job lifecycle with idempotency and locking (ADR 0003); the outbox (ADR 0006);
Alembic-only schema with real constraints (ADR 0002); and a resolved deployment topology —
wallet sync is a continuous background workload and does not fit a once-daily 300-second
cron.

---

## 7. Legal, privacy, and workplace considerations

**This section is not legal advice, and this design cannot substitute for it.**

Whether and how this system may lawfully be deployed depends on jurisdiction, on the
relationship between the company and the people whose addresses may appear, and on what
was disclosed to them. The following must be resolved **before** implementation begins,
not after:

- **Employment and workplace-monitoring law** varies substantially by jurisdiction.
  Monitoring that relates to employees may require notice, consent, consultation with a
  works council or employee representatives, or may be restricted outright.
- **Data protection law** (GDPR and equivalents): a blockchain address, once associated
  with a person, is personal data. That triggers obligations around lawful basis,
  purpose limitation, data minimization, retention limits, and subject access — including
  the right to contest automated inferences.
- **Retention.** Chain data is permanent; your copy of it need not be. Define a retention
  policy for `RiskSignal`, `AddressLabel`, and raw provider payloads.
- **Accuracy and contestability.** Because labels are human assertions that can be wrong,
  and signals are patterns that usually have innocent explanations, there must be a
  documented way for a labelled party's position to be recorded and for a signal to be
  marked `FALSE_POSITIVE` without deleting the audit trail.
- **Access control.** This is the most sensitive data the application would hold. It needs
  authentication and authorization — which the application does not currently have at all
  (`docs/SECURITY.md` §1.1). That gap must close before Treasury Audit ships.
- **Purpose limitation in practice.** The `watch_reason` field on `WatchedWallet` exists so
  that the reason an address is watched is recorded at the time it is added. Keep it
  required.

If the intended use involves monitoring individuals rather than reconciling company
treasury movements, that is a materially different system with materially different legal
exposure, and it is out of scope for this design.

---

## 8. Explicitly out of scope

Custody or signing of any kind. Automated freezing, blocking, or reporting. Automated
identity attribution from on-chain behaviour. Chain-analysis-grade clustering heuristics.
Any automated accusation, score, or adverse action against a person. Trading, portfolio
management, or investment functionality of any kind.
