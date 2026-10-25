# ADR-009: Per-Tenant Audit Hash Chain

**Status**: Accepted (design; implementation follows in separate PRs)  
**Date**: 2026-10-25

---

## Context

ZizkaDB is sold as a tamper-evident record of agent actions (README, EU AI Act Article 12). Today each event carries a v2 checksum (`core/services/checksum.py`) covering every immutable audit field, and `GET /v1/events/verify` recomputes it row by row. That catches an edited row whose checksum was left alone. It does not catch:

- a deleted, reordered or inserted row;
- an edit by anyone who also recomputes that row's checksum, since the hash is unkeyed and stored in the same row.

Constraints from the current code:

- **One canonical form already exists.** The write path has Postgres canonicalise values (`$n::jsonb`, `$n::uuid`, `nextval`, `now()`) and hashes them with `compute_row_checksum_v2`, the same function the verifier uses.
- **`sequence_no` is a global `BIGSERIAL`.** It is taken by `nextval` before the insert, so concurrent writes can commit out of `sequence_no` order, and rolled-back writes leave gaps.
- **Writes already take locks.** A write takes a row lock on `agents`, plus `pg_advisory_xact_lock(hashtext(tenant_id))` for a new agent. Forget and the embed worker take per-event advisory locks. Forget calls the search index inside its transaction.
- **Lawful deletion exists:**
  - `DELETE /v1/memory/forget` removes matching events;
  - agent delete removes all of an agent's events;
  - account delete removes the tenant;
  - a retention purge is planned but not built.
- **Erased parents.** PR #223 drops the `parent_event_id` foreign key so that children keep their link (and a valid checksum) when a parent is erased.
- **Self-hosted is one agent per tenant.** ZizkaDB Cloud tenants can run many agents writing concurrently.

The owner decided on a per-tenant hash chain plus a verify endpoint (hardening plan, Days 9–12).

---

## Decision

### 1. One chain per tenant, with its own contiguous index

Every event written after the upgrade gets a per-tenant position `chain_index` (1, 2, 3 … with no gaps) and a `chain_hash`. A new table holds each tenant's head:

```sql
audit_chain_heads (
    tenant_id  UUID PRIMARY KEY,  -- no foreign key; see the account-delete note
    head_index BIGINT NOT NULL,
    head_hash  TEXT   NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
```

- **Appending.** Inside the existing write transaction, the writer locks its tenant's head row (`INSERT … ON CONFLICT DO NOTHING`, then `SELECT … FOR UPDATE`). It takes `head_index + 1`, inserts the event with its `chain_hash`, and updates the head in the same transaction.
- **Why a contiguous index.** Contiguity makes a deleted row visible as a gap even before any hash is checked. Ordering by the global `sequence_no` would need `nextval` to run under the lock; the chain index needs nothing else.
- **Why a head row.** A row lock replaces a hashed advisory key, so there are no key collisions with the existing advisory locks. It also stores the head, which the verifier compares against the last entry to catch a deleted tail.
- **Lock order rule.** The head lock is the **last application lock a transaction takes**: after the `agents` row lock and any advisory lock. No network call (Qdrant, embeddings) runs while it is held. A write already takes its locks in that order. Its `INSERT` still takes foreign-key share locks after the head:
  - on `tenants` (see the account-delete note below);
  - on the parent event, while `parent_event_id` still has a foreign key.
- **The parent foreign key must go first (#223).** While it exists, a write of a child holds the head and waits for a share lock on the parent row. A forget or agent delete of that parent holds the row and waits for the head to write its receipt. That is a deadlock (see the prototype results); with #223's dropped key it doesn't happen.
- **Agent delete** must start with `SELECT … FROM agents … FOR UPDATE` and only then delete events and take the head. Two things follow:
  - Locked in the other order, it deadlocks against a write (see the prototype results).
  - Today's delete (`core/api/agents.py`) deletes events first and touches the `agents` row last. A write that commits in between leaves an event that blocks the final `DELETE FROM agents` with a foreign-key error, a race that already exists on `main` (#305). Locking the agent first removes that error.
  - A write that was waiting on the row then runs after the delete and creates the agent again, as any later write would. It skipped the plan's agent-slot check because the agent existed when it looked, so #305's fix should repeat that check when its upsert inserts rather than updates.
  - Its `UPDATE events SET parent_event_id = NULL` on rows that are about to be deleted is dropped.
- **Account delete** (`DELETE FROM tenants`) already deadlocks with a concurrent write on `main`. The write holds the `agents` row and waits for a share lock on `tenants`; the delete holds `tenants` and cascades into `agents`. Postgres aborts one side (#306).
- **New tables take no foreign key to `tenants`.** The chain must not add new paths into that deadlock, and a tombstone or receipt `INSERT` with such a key would take a share lock on `tenants` while forget holds event row locks. So the new tables carry `tenant_id` without a foreign key, and `delete_managed_account` deletes their rows explicitly **after** `DELETE FROM tenants`, in the same transaction. The write path then takes the same locks as today, plus the head row. Two reasons for that order:
  - Deleting `audit_chain_heads` first would take the head lock before the `agents` rows, which breaks the lock order rule.
  - By the time the cascade from `tenants` has finished, it has waited out any in-flight write or erasure, so their committed chain rows are visible and get deleted too.

### 2. The link hashes the v2 checksum, recomputed

```
chain_hash = H( "zizkadb-chain-v1" \n tenant_id \n chain_index \n prev_hash \n entry_kind \n entry_id \n entry_digest )
```

- **Event entries.**
  - `entry_kind` is `event`;
  - `entry_id` is the `event_id`, generated in the canonical `SELECT` with `gen_random_uuid()` so it is known before the insert;
  - `entry_digest` is the row's v2 checksum.
- **For live events, the digest is recomputed, never read back.** The verifier recomputes `entry_digest` from the row's columns, not from the stored `checksum`. An edit therefore breaks the chain even if the attacker also rewrites the stored checksum.
- **Genesis.** For each tenant's first entry, `prev_hash` is `genesis:<legacy_until_seq>` (see 5). This binds the chain's start into the first hash, so the cutoff can't be moved without breaking it.
- **No second canonical form.** The chain reuses checksum v2's canonicalisation, so the two can never disagree about what an event's content is. Changing v2's preimage now also changes the chain, which is intended: v2 already promises stability.
- **Excluded columns.** The embedding pipeline's columns (`embedding`, `index_status`, `index_attempts`, `index_retry_at`) stay outside both hashes, so the embed worker's updates never affect verification.

### 3. Optional key held outside the database

- **`H` per entry.** `H` is SHA-256, or HMAC-SHA256 when `AUDIT_CHAIN_KEY` is set.
- **Recording which key was used.** Each entry stores `chain_alg`: `sha256` or `hmac-sha256:<key_id>`. The verifier uses it only to pick which key to check with.
  - `key_id` is the first 8 hex characters of HMAC-SHA256(key, `"zizkadb-chain-key-id"`), not a plain hash of the key, so database readers can't test guesses against it offline.
  - The API refuses to start with a key shorter than 32 random bytes (64 hex characters).
- **No downgrade.** When the verifier has a key configured, **every** chained entry must be keyed; an unkeyed one is reported as `unkeyed_entry`. Otherwise a database writer could recompute a tail with plain SHA-256, label it `sha256` and pass. (This is the same downgrade that `checksum_v2_cutoff` blocks for v1 checksums.)
- **Choosing a key.** A deployment therefore chooses keyed or unkeyed before its chain starts. Turning a key on for a chain that already has unkeyed entries needs a switch point recorded outside the database, and is deferred.
- **Rotation.** Keyed to keyed rotation is supported. `AUDIT_CHAIN_KEYS_OLD` lists retired keys, oldest first, used for verification only.
  - Along a chain, keys may only move forward: once an entry uses a newer key, an entry with an older key is reported as `retired_key`. So someone holding a leaked old key can't append after the rotation.
  - They can still truncate back to before the rotation and rewrite from there with the old key. That is caught only against a receipt.
  - Rotating after a leak doesn't restore trust in entries written while the key was exposed.
- **Unverifiable entries.** An entry whose `key_id` matches no configured key is reported as `unknown_key`. A verifier with no key configured reports keyed entries as `unknown_key` too: it can't check them, so it doesn't claim they are fine.
- **Losing the key.** A lost key makes its entries unverifiable. The setting is documented with that warning and is off by default.
- **ZizkaDB Cloud.** Should set a key held outside the database host.

### 4. Lawful erasure leaves tombstones, sealed by a chained receipt

Forget, agent delete and any future retention purge must, in the same transaction:

- **Tombstones from what was actually deleted.** Generate a `receipt_id` (UUID) up front, then delete with `DELETE … RETURNING` and insert the tombstones from that result: `tenant_id`, `chain_index`, `event_id`, `entry_digest`, `chain_alg`, `chain_hash`, `receipt_id`.
  - `entry_digest` is the deleted row's stored v2 `checksum`, copied in SQL (`INSERT … SELECT … FROM (DELETE … RETURNING …)`), so no payload is sent to the API. With it, the verifier can recompute the tombstone's link exactly like a live event's.
  - A tampered stored checksum is still caught, because the tombstone's link would no longer match the `chain_hash` written at insert. Content edited while its checksum was left alone is not caught after the erasure, but that content is gone anyway.
  - Without a digest, every erasure would reset the chain: an edit just before a tombstone would only need to be recomputed up to that tombstone. `event_id` is covered by that hash too.
  - Recomputing the digest from the payload in Python would instead pull every erased row, up to a whole agent's history, into one result set while the agent's writers are blocked.
  - No content is kept. The digest allows the same guess confirmation as `chain_hash`; see below.
  - Taking tombstones from the `DELETE` itself, not from an earlier `SELECT`, means two overlapping erasures can't both tombstone the same row. Today, forget builds its id list from an unlocked `SELECT`.
  - Linking by `receipt_id` instead of the receipt's chain index lets the bulk move happen before the head lock, so a large agent delete doesn't block writers.
- **Receipt.** Then take the head lock and append one **erasure receipt** to the chain, stored in `audit_chain_receipts` (`receipt_id`, `chain_index`, `reason`, `erased_count`, `erased_digest`, `chain_hash`, `chain_alg`):
  - `entry_kind` is `erasure`;
  - `entry_id` is `receipt_id`;
  - `entry_digest`, stored as `erased_digest`, is SHA-256 of the UTF-8 text `"<chain_index>:<chain_hash>\n"` for each tombstone in ascending `chain_index` order, followed by `"<reason>\n<erased_count>"`.
  - The list comes first so the verifier can hash tombstones as it walks past them, keeping one running hash per open receipt instead of buffering the list. Postgres computes the same bytes with `sha256(convert_to(string_agg(chain_index || ':' || chain_hash || E'\n', '' ORDER BY chain_index) || reason || E'\n' || count, 'UTF8'))`.
  - `erased_count` counts **chained rows only**, the tombstones. Unchained rows deleted by the same erasure are not counted here; the API response still reports the total, as today.
- **No content in the reason.** `reason` is a fixed value: `forget`, `agent_delete` or `retention`. It never contains the forget filter or its value, which are usually personal data and would otherwise be locked into the chain for good.
- **Unchained rows.** An erasure that deletes only unchained rows (see 5) writes no tombstone and no receipt: there is nothing in the chain to account for.

The verifier accepts a tombstone only if a later receipt in the chain covers it, with matching count and digest.

**What this protects, honestly.** A tombstone on its own can be forged: delete a row, insert a fake tombstone, and the gap looks lawful. A receipt stops that **only with a key**. Without one, an attacker can append a forged receipt at the tail: no existing link changes, and even a receipt the caller stored earlier still matches. So without a key, an unlawful deletion can be dressed up as a lawful erasure. It is still visible as an erasure the application never logged, but the verifier can't tell the difference. With a key, a receipt can't be produced without it.

Further rules:
- **Agent delete** writes one receipt with a digest, not one row per event, so even an agent with a million events costs one chain entry.
- **Account delete** removes the tenant, its head and its whole chain: `delete_managed_account` deletes the chain tables' rows right after the tenant, in the same transaction (Decision 1). Nothing is left to verify, and nothing is claimed.
- **Erased parents** rely on #223: a child keeps the erased parent's id, so its checksum and link stay valid. #223 must merge before the write path ships, for this reason and for the lock order (Decision 1).
- **Guess confirmation.** A tombstone's `entry_digest` (an unkeyed checksum) lets someone with database access who already knows an erased event's exact content confirm that it existed. Its `chain_hash` does the same when there is no key. The content itself is gone. This is noted in the erasure docs.

### 5. Events from before the chain stay unchained

- **No backfill.** Rows written before the upgrade keep `chain_index = NULL` and are reported as `unchained` by count. Each tenant's chain starts at its first event after the upgrade.
- **The chain's start is recorded.** `audit_chain_cutoff.legacy_until_seq` is written once, by the **first chained write** (`INSERT … ON CONFLICT DO NOTHING` inside that write's transaction), not by `init_db()`.
  - The statement must run **before** that write takes its own `sequence_no`. Concurrent first writes then serialise on the singleton row, and every chained row lands above the cutoff. Run after `nextval`, every concurrent first write lands at or below it (see the prototype results).
  - Once a process has seen the row, it can skip the statement. The embed worker also runs `init_db()`, and in `infra/docker-compose.yml` it can start on the new image while the old API is still writing. A row with `chain_index = NULL` above that `sequence_no` is reported as `unchained_after_start`, not counted. Otherwise a row inserted directly into `events` after the upgrade would pass as old.
  - The value comes from the sequence, not `MAX(sequence_no)`, so writes in flight at upgrade time stay below it. `checksum_v2_cutoff` uses `MAX` and has that gap (#307).
  - Exactly: `CASE WHEN is_called THEN last_value ELSE last_value - 1 END`. On a sequence that has never been used, `last_value` is already its start value (1). Taking it as is would put a fresh install's first event at the cutoff instead of above it, and every new install would fail.
  - This relies on the sequence's `CACHE 1` (the `BIGSERIAL` default). With a larger cache, another session could still hand out lower cached values after the cutoff.
  - Old-code API processes must be stopped before new ones start writing. Recording the cutoff at the first chained write means only the API's own restart matters, not the embed worker's.
  - The cutoff is bound into each tenant's genesis hash (see 2), and the verifier also requires every chained row's `sequence_no` to be above it. Editing the cutoff row therefore breaks the chain.
- **Seed scripts.**
  - `seed-demo-data.py` (`COPY`) and `seed-token-usage-events.py` (a raw `INSERT` path) write backdated rows into `events` directly. `write_event` stamps `now()`, so routing them through it would break their purpose. They stay development tools, and the docs say that a tenant seeded this way reports `unchained_after_start`. `seed-demo-data.py --reset` removes those unchained rows, after which the chain verifies again.
  - `seed-support-bot-events.py` and `seed-token-optimization-deterministic.py` log through the SDK, so their events are chained normally. So is the single image's first-boot demo (`infra/allinone/seed_demo.py`, through the HTTP API).
- **Limit.** The region before the chain is not protected. A database writer can still insert a backdated row with an old `sequence_no`, as they always could.
- **Why not backfill.** A backfill would attest history as of the backfill date while looking like it was attested at write time, which is misleading.
- **Effect on the plan.** The plan's "backfill script" becomes the one-time cutoff written by the first chained write.

### 6. Verify endpoint

`GET /v1/events/verify/chain`, with dashboard session only (`require_dashboard_session`).

- **Why a new path.** The existing `GET /v1/events/verify` (per-agent row checksums, API keys allowed) is unchanged. `/verify/chain` is used rather than `/chain/verify`, because the existing `/{event_id}/verify` route would capture `/chain/verify` (checked against the router).
- **Why session-only.** The chain spans every agent in the tenant, and the result names event ids, so agent-scoped keys must not call it. Unscoped API-key access for auditors, and an SDK method on top of it, are deferred: the SDKs authenticate only with API keys, so they can't call a session-only route.

Behaviour:
- **Consistent read.** Reads in a `REPEATABLE READ READ ONLY` transaction, so concurrent writes and erasures are seen consistently.
- **Merged walk.** Streams events, tombstones and receipts merged by `chain_index`, in key-set pages of 1,000. Memory stays flat on large tenants: receipt digests are hashed incrementally (Decision 4).
- **Optional receipt check.** `expected_head_index` and `expected_head_hash` let a caller check a receipt they stored earlier. A write returns `chain_index` and `chain_hash` for that purpose, and both SDKs' log results must carry them. Today they copy only known fields, so a receipt would be dropped.

Response:

```json
{
  "ok": true,
  "checked": 1200, "erased": 3, "receipts": 1, "unchained": 5400,
  "head": {"chain_index": 1204, "chain_hash": "…"},
  "first_problem": null
}
```

`first_problem` is `{kind, chain_index, event_id}`. `kind` is one of:

| `kind` | Meaning |
|---|---|
| `hash_mismatch` | The entry's recomputed hash doesn't match. Its content changed, or it was moved: the index is part of the hash, so a swap shows up here. `prev_hash` is not stored, so a changed link and changed content look the same. |
| `gap` | A missing index with no tombstone |
| `duplicate_index` | The same `chain_index` in more than one table, for example an erased event restored next to its tombstone. `(tenant_id, chain_index)` is unique only within each table, so the verifier checks across all three. |
| `unkeyed_entry` | An unkeyed entry while the verifier has a key configured |
| `unknown_key` | An entry keyed with a key the verifier doesn't have |
| `retired_key` | An entry keyed with an older key after a newer key already appeared |
| `unchained_after_start` | A row without `chain_index` written after the chain started |
| `unsealed_tombstone` | A tombstone that no later receipt covers |
| `head_mismatch` | The last entry is not the stored head. This catches a tail deleted without also rolling the head back. The head is not keyed, so a rollback is caught only through `receipt_mismatch`. |
| `receipt_mismatch` | The chain disagrees with the caller's expected head |

The hash values themselves are never returned for a failing entry, for the same reason `verify_stored_checksum` hides them.

### 7. Prototype results

A throwaway prototype (simplified tables, the same lock and hash scheme, local Postgres 16 on a laptop) tested the decisions above before writing them down:

| Check | Result |
|---|---|
| 5,000 chained writes from 20 concurrent writers, one tenant | Indexes 1–5,000, no gaps or duplicates; chain verifies |
| 20 tenants, 20 concurrent writers | Every chain verifies |
| Edited row / deleted row / deleted newest row / swapped rows | `hash_mismatch` / `gap` / `head_mismatch` / `hash_mismatch` |
| Agent delete takes the head lock before the `agents` row | `DeadlockDetectedError` against a concurrent write |
| Agent delete takes the head lock last | No error |
| Model of the verifier: real erasure, then a write | ok, with and without key |
| Model: planted tombstone with no receipt | `unsealed_tombstone`, with and without key |
| Model: edited row just before a tombstone, only that row recomputed, no key | Passes if tombstones keep only `chain_hash`; `hash_mismatch` once they keep `entry_digest` (Decision 4) |
| Model: forged receipt appended at the tail | Passes without key; `unkeyed_entry` with key |
| Model: edited row, tail recomputed unkeyed | Passes without key; `unkeyed_entry` with key |
| Model: newest event deleted, head rolled back | Passes with and without key (only a stored receipt catches it) |
| Forget of a parent vs a concurrent write of its child, under this lock order, parent foreign key present (`main`'s schema) | `DeadlockDetectedError` |
| The same, parent foreign key dropped as in #223 | No error |
| Fresh install: `last_value` vs the first event's `sequence_no` | Both 1 (`is_called` false), so the cutoff must use `is_called` |
| Receipt digest: the Postgres expression in Decision 4 vs a streaming Python hash | Identical, with unsorted input and a `chain_index` above 2³¹ |
| Cutoff written by the first chained write, 20 rounds of 30 concurrent first writes | Written after the write's own `nextval`: 600 of 600 rows at or below the cutoff. Written before: 0 |
| Today's agent delete vs a concurrent write (`main`'s schema) | `ForeignKeyViolationError` on `fk_agent` |
| Today's account delete vs a concurrent write (`main`'s schema) | `DeadlockDetectedError` |
| Throughput, one tenant, 20 concurrent writers | ~3,950 writes/s without chain → ~1,130 with chain |
| Throughput, one tenant, one writer | ~510 writes/s with chain |
| Throughput, 20 tenants, 20 concurrent writers | ~3,750 writes/s with chain (tenants don't block each other) |

These figures come from simplified tables, so they only show the shape of the cost: writers within one tenant serialise, and different tenants don't block each other. Day 10 measures the real write path.

---

## Consequences

**What this detects.** "DB writer" means anyone who can change rows in the database; "receipt" means a `(chain_index, chain_hash)` the caller stored outside the database.

| Change by a DB writer | Without key | With key held outside the DB |
|---|---|---|
| Edit to any audited field of a chained event | ✔, unless every later link and the head are recomputed | ✔ |
| Reordered events, or an event inserted into the middle of the chain | ✔, unless the tail is recomputed | ✔ |
| Fabricated event appended at the end (one new hash, head updated) | ✘ nothing earlier changes; a receipt can't tell it from normal growth | ✔ |
| Deleted event in the middle | ✔ `gap`, unless dressed up as an erasure (next row) | ✔ |
| Deletion dressed up as a lawful erasure (fake tombstone and receipt) | ✘ the verifier can't tell it apart | ✔ |
| Deleted newest events | ✔ if the head was left alone; otherwise only against a receipt | same as without key: the head is not keyed |
| Row added outside the chain after it started | ✔ `unchained_after_start` | ✔ |
| Erased event restored from a backup next to its tombstone | ✔ `duplicate_index` | ✔ |
| Backdated row added among events from before the chain | ✘ | ✘ |
| Entries rewritten as unkeyed | n/a | ✔ `unkeyed_entry` |
| Tail rewritten with a leaked, since-retired key | n/a | ✔ after the rotation point (`retired_key`); a rewrite from before it only against a receipt |
| Chain start (cutoff) moved | ✔ unless the whole chain is recomputed | ✔ |
| Full rewrite of chain and head | ✘ (a receipt shows rewritten history up to its index) | ✔ for every entry; truncation only against a receipt |

Without a key, the chain protects against accidental and partial changes, and against anyone who doesn't recompute it. It does not protect against a deliberate database writer. The README and trust pages must say exactly that.

**Better:**
- the README's tamper-evidence claim becomes checkable, with its limits stated;
- one canonical form for checksum and chain;
- no second write to the event row: the chain fields go in the existing `INSERT`. The cost is one head-row `UPDATE` per event.

**Worse:**
- **Per-tenant serialisation.** Writes within one tenant serialise from the head lock to commit. Day 10 measures single-tenant latency before and after with a 1,000-event loop. If p50 latency more than doubles, or a Cloud tenant needs more single-tenant throughput than the measured ceiling (the prototype reached ~1,130 writes/s with 20 writers), revisit with per-agent chains (see below).
- **Lock order becomes an invariant.** A new transaction that takes the head lock early can deadlock.
- **Erasure costs more.** Every erasure path must write tombstones and a receipt. A path that deletes events without them shows up as tampering, which is intended.
- **The key is a liability.** Losing `AUDIT_CHAIN_KEY` makes keyed entries unverifiable.

**Accepted limitations:**
- Without a key, a deliberate database writer is not detected: they can recompute the chain, append fabricated events or forge an erasure.
- With or without a key, deleting the newest events and rolling the head back is caught only against a receipt stored outside the database. External anchoring is the general fix (deferred).
- Events written before the upgrade are counted, not verified, and that region accepts backdated inserts.

---

## Alternatives considered

- **Order by the global `sequence_no` (PR #225).**
  - It works only if `nextval` runs under the tenant lock.
  - Gaps from rolled-back writes are normal, so a deleted row can't be told apart from a burnt sequence number.
  - Finding the head needs a `UNION … ORDER BY sequence_no DESC LIMIT 1` on every write.
- **Hash `data::text` / `metadata::text` (PR #225).** This creates a second canonical form next to checksum v2, and the two can drift.
- **Insert, then `UPDATE` the row with its hash (PR #225).** This doubles the row versions written per event and leaves a moment where a row has no hash. The v2 write path already avoids both.
- **Per-tenant advisory lock on a hashed key.** It shares one 64-bit key space with the existing `hashtext` locks, and it still needs somewhere to store the head. The head row does both.
- **One chain per agent.** It gives parallel writes for multi-agent Cloud tenants, but:
  - agent delete removes a whole chain, so a tenant-level receipt is needed anyway;
  - cross-agent reordering is not covered.
  
  Kept as the fallback if Day 10's measurements fail the threshold above.
- **Tombstones without receipts (PR #225's `event_erasures`).** A fake tombstone hides a deletion. Receipts close this only with a key (Decision 4).
- **Accept a nulled parent if it points at an erased event (PR #225).** This is unnecessary once #223 keeps links, and it costs one hash per erased event per row checked.
- **Backfill existing rows into the chain.** Misleading; see Decision 5.
- **External anchoring** (publishing the head to a transparency log or timestamp authority). It would catch a full rewrite without a key, and a truncated tail with a rolled-back head with or without one. Deferred; the stored-receipt parameter is its building block.

---

## Implementation plan (hardening plan Days 10–12)

1. **Day 10, schema and write path** (after #223 has merged; see Decision 1).
   - Columns `chain_index`, `chain_hash` and `chain_alg` on `events`. A unique `(tenant_id, chain_index)` on `events`, `audit_chain_tombstones` and `audit_chain_receipts`.
   - On `events`, make the index partial (`WHERE chain_index IS NOT NULL`). Even so, building it in `init_db()` scans the whole table and blocks writes until it finishes, and `init_db()` runs in every API worker and the embed worker. Measure it on a large table.
   - `CREATE INDEX CONCURRENTLY` can't go in `init_db()`: it is rejected inside a multi-statement execute, and parallel builds can leave an INVALID index that `IF NOT EXISTS` then skips for good. If the plain build is too slow, offer `CONCURRENTLY` as a documented manual step before upgrading, and have `init_db()` fail loudly on an INVALID copy.
   - If `AUDIT_CHAIN_KEY` is wired into the single image (`infra/allinone/`), update `redu.md`, the README single-container section and `wiki/Self-Hosting.md` in the same PR (CLAUDE.md rule). Keep in mind that a key generated into `/data/secrets.env` sits next to the database, so it doesn't protect against someone who holds the whole volume.
   - The four new tables (`audit_chain_heads`, `audit_chain_tombstones`, `audit_chain_receipts`, `audit_chain_cutoff`), in both `schema.sql` and `init_db()`.
   - Docs: tenants seeded by the two raw-insert scripts report `unchained_after_start`.
   - Append in `write_event`; the write response gains `chain_index` and `chain_hash`. Every route that returns a `write_event` result passes them on: `POST /v1/events`, A2A, the agent test event and the dashboard connection test. Python and TypeScript SDK log results carry them, with the SDK version-bump rule.
   - Same PR, per CLAUDE.md: `dashboard/lib/api.ts` types, KB §17.3 (response shape) and KB §21 (schema).
   - Real-Postgres tests:
     - concurrent first writes across tenants all land above the cutoff (fails if the cutoff is recorded after `nextval`);
     - a fresh install's first event verifies;
     - concurrent writers in one tenant give a gap-free chain.
   - Latency measured before and after.
2. **Day 11, erasure and verify.**
   - Tombstones and receipts in forget and agent delete, under the lock order rule, with agent delete locking its `agents` row first.
   - `GET /v1/events/verify/chain`, registered and tested so `/{event_id}/verify` can't capture it; `delete_managed_account` deletes the chain tables' rows right after `DELETE FROM tenants`. KB and `api.ts` in the same PR.
   - Real-Postgres tests for each `kind`, concurrent writers, a concurrent forget during verify, and the agent-delete lock order (a test that deadlocks if the head is locked first).
3. **Day 12, surface.**
   - A dashboard view of the verify result (no SDK method; see Decision 6).
   - README and trust-page wording, matching the table above.

PR #225 is the starting point for the code: tests, the HMAC option, the tombstone idea and the verifier loop carry over. The changes are listed in the review on #225.
