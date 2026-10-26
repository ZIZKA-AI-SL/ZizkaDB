# ADR-009: Audit Hash Chain

**Status**: Accepted. Part 1 records what shipped in PR #310. Part 2 is the hardening plan, implemented in separate PRs.  
**Date**: 2026-10-26 (first draft 2026-10-25, revised after PR #310 merged)

---

## Context

ZizkaDB is sold as a tamper-evident record of agent actions (README, EU AI Act Article 12). Each event carries a v2 checksum (`core/services/checksum.py`) over every immutable audit field, and `GET /v1/events/verify` recomputes it row by row. A row checksum alone catches an edited row whose checksum was left alone. It cannot catch a deleted, reordered or inserted row, or an edit by someone who also recomputes the checksum.

PR #310 added a hash chain to close the ordering gap. The first draft of this ADR proposed a different, per-tenant design before #310 merged. This revision records the shipped design as the decision, keeps the analysis from the draft that still applies, and plans the hardening on top of what shipped.

---

## Part 1: What shipped (PR #310)

### One chain per agent

- **Link.** Each event stores `chain_hash = sha256(prev_chain_hash + ":" + checksum)` (`core/services/audit_chain.py`). `prev_chain_hash` is the agent's newest link, from either `events` or `event_tombstones`, by `sequence_no`. The first link uses an empty previous hash.
- **Serialisation.** The write path (`core/services/event_write.py`) takes the `agents` row lock with `INSERT … ON CONFLICT DO UPDATE` before it calls `nextval` and reads the previous link, all in one transaction. One agent's chain therefore cannot fork, and its `sequence_no` order matches its chain order.
- **Unkeyed.** The hash is plain SHA-256. There is no key option.
- **Events from before the chain** have `chain_hash = NULL` and are counted as `unchained_legacy`. A row without a link that appears after the agent's first link is reported as `unchained_after_chain_start`.
- **Excluded columns.** The embedding pipeline's columns (`embedding`, `index_status`, `index_attempts`, `index_retry_at`) stay outside the checksum and the chain, so the embed worker's updates never affect verification.
- **Seed scripts.** `seed-demo-data.py` (`COPY`) and `seed-token-usage-events.py` (raw `INSERT`) write backdated rows straight into `events` without a link. An agent seeded that way after its first chained event reports `unchained_after_chain_start`. They are development tools; the scripts that log through the SDK produce chained events.

### Tombstones for lawful deletion

- **Forget** (`DELETE /v1/memory/forget`) and the **retention purge** (`core/services/retention.py`) call `tombstone_events()` before deleting (replaced by `delete_and_tombstone()` in #321, see H2). It copies each row's `tenant_id`, `agent_id`, `event_id`, `sequence_no`, `chain_hash` and a fixed `reason` into `event_tombstones`. No content is kept.
- **Agent delete** removes the agent's events and its tombstones. Its chain is gone, and nothing is left to verify.
- **Account delete** cascades from `tenants`, which also removes `event_tombstones`.

### Verify endpoint

- `GET /v1/events/chain?agent=…` uses `get_tenant` plus `assert_agent_allowed`, so API keys can call it and scoped keys only see their own agent.
- It walks events and tombstones merged by `sequence_no`, in pages of 2,000. It rechecks each event's checksum and link, and returns `intact`, a summary, up to 100 problems and `chain_head`.
- **Anchoring.** A caller stores `chain_head` outside ZizkaDB and later passes `after_seq` and `anchor`. That proves nothing before the stored head was rewritten.
- **SDKs.** Python `verify_chain()` and TypeScript `verifyChain()` wrap it. Log results carry `chain_hash`.

### Known gaps in the shipped design

Each gap below was checked against `main`. The first two were reproduced by running `verify_chain()` from `main` against in-memory rows. Both cases return `intact: true`.

| Gap | Why it happens |
|---|---|
| **An edit just before a tombstone passes.** Edit event *k*, recompute only its checksum and its own link. If event *k+1* was erased, the chain still verifies. | The verifier never recomputes a tombstone's link. It takes the tombstone's stored `chain_hash` as the next `prev`, so the chain restarts at every tombstone. |
| **A deletion can be dressed up as lawful.** Delete a row and insert a tombstone that copies its `chain_hash`. | A tombstone needs nothing a database writer lacks. There is no receipt for an erasure. |
| **The newest events can be deleted.** | No head is stored, so only a caller's own anchor catches a missing tail. |
| **A full rewrite passes.** | The chain is unkeyed. Only an anchor stored outside the database catches it. |
| **Overlapping erasures race.** | Forget builds its id list from an unlocked `SELECT`, then tombstones by id. Tombstones use `ON CONFLICT DO NOTHING`, which hides a double tombstone, but the count in the response can be wrong. |
| **Cross-agent order is not covered.** | Each agent has its own chain. Moving an event between agents breaks both chains, but reordering across agents is invisible. Accepted: see Alternatives. |

---

## Part 2: Hardening plan

Keep the per-agent chain, the route and the SDK methods. Every change below is additive: existing `chain_hash` values stay valid, and the response only gains fields and problem kinds.

### H1. Tombstones keep the deleted row's checksum, and the verifier recomputes their link (done, #321)

- Add `checksum VARCHAR(64)` to `event_tombstones`. `delete_and_tombstone()` (which replaced `tombstone_events()`) copies the row's stored checksum in SQL, so no payload reaches the API.
- The verifier checks `tombstone.chain_hash == sha256(prev + ":" + tombstone.checksum)`, exactly as for an event. That closes the restart at every tombstone.
- **Older images write checkable tombstones too.** A `BEFORE INSERT` trigger on `event_tombstones` (`zizkadb_tombstone_checksum()`) fills a missing checksum from the row being deleted. 0.5.0 inserts its tombstone before deleting the row, so its erasures during a rolling deploy or after a downgrade carry a checksum and verify normally.
- **No new constraint.** A `CHECK (checksum IS NOT NULL)` would make a downgraded 0.5.0 forget fail; the trigger makes it unnecessary.
- **Tombstones from before the upgrade** have no checksum. They are accepted as `legacy_tombstones` **only if deleted before `tombstone_checksum_cutoff.since`**.
  - `init_db()` records that cutoff once, in a statement that runs after the trigger is committed, so every tombstone written afterwards has a checksum, whichever image wrote it.
  - After the cutoff, a missing checksum is reported as `tombstone_missing_checksum`. Without that bound, blanking one column would undo H1.
- **Limit (no key).** The cutoff is an ordinary database row. A deliberate database writer can blank checksums and also delete or move the cutoff row, or backdate `deleted_at`, and H1 no longer catches the edit before that tombstone. This is within the no-key limits in Consequences; only H4's key closes it.
- **Privacy.** The checksum lets someone with database access who already knows an erased event's exact content confirm that it existed. `chain_hash` already allows the same guess today. The content itself is gone. The README's forget entry and the CHANGELOG say this.

### H2. Tombstones come from the `DELETE` itself (done, #321)

- Forget and retention insert tombstones with `INSERT … SELECT … FROM (DELETE … RETURNING …)`, so two overlapping erasures cannot both account for the same row, and the reported count is the number actually deleted.
- **Lock order.** Forget and retention take the per-event advisory locks (key `hashtext(event_id)`, the embed worker's) before any row lock, in one statement (`lock_events()` in `core/services/audit_chain.py`).
  - The locks go in byte-wise id order (`COLLATE "C"`), the order 0.5.0 used. So an older and a newer image erasing the same events during a rolling deploy lock in one order and can't deadlock.
  - Two ids sharing a 32-bit key could still interleave badly. Postgres then aborts one transaction; that needs two such collisions, and the erasure can be retried.
  - Retention used to lock rows first (`FOR UPDATE SKIP LOCKED`) and could deadlock with a forget. It now waits for the forget instead, and counts only the rows still there.

### H3. A chained erasure receipt per erasure, per agent

- Each forget or retention batch appends one receipt to every affected agent's chain, under that agent's `agents` row lock. It takes a `sequence_no` from the same sequence, so it merges into the walk like an event.
- New table `event_erasure_receipts` (`tenant_id`, `agent_id`, `receipt_id`, `sequence_no`, `reason`, `erased_count`, `erased_digest`, `chain_hash`). It has no foreign key to `tenants`; account delete removes its rows explicitly, after `DELETE FROM tenants`, in the same transaction.
- `erased_digest` is SHA-256 over `"<sequence_no>:<chain_hash>\n"` for each tombstone in `sequence_no` order, then `"<reason>\n<erased_count>"`. Tombstones gain `receipt_id`.
- The verifier accepts a tombstone only if a later receipt covers it with a matching count and digest, and reports `unsealed_tombstone` otherwise.
- `reason` is a fixed value: `forget` or `retention`. It never contains the forget filter, which is usually personal data.
- **Lock order.** Forget already holds per-event advisory locks when it takes the agent row lock for the receipt. The write path takes the agent row lock and no per-event advisory lock (a new agent also takes the tenant advisory lock from `agent_limits`). Retention runs one agent per transaction. Forget across several agents must lock their `agents` rows in sorted `agent_id` order. A real-Postgres test must show no deadlock between a forget and concurrent writes before this ships.
- **Honest limit.** Without a key, anyone with database write access can forge a receipt too. Receipts only make a fake erasure detectable with a key (H4).

### H4. Optional key held outside the database

- `AUDIT_CHAIN_KEY` switches the link from SHA-256 to HMAC-SHA256. Each link records its algorithm in a new `chain_alg` column: `sha256` or `hmac-sha256:<key_id>`.
- `key_id` is the first 8 hex characters of HMAC-SHA256(key, `"zizkadb-chain-key-id"`), so database readers cannot test key guesses offline against it. The API refuses to start with a key shorter than 32 bytes.
- **No downgrade.** With a key configured, the verifier reports any unkeyed link after the first keyed one as `unkeyed_entry`. Otherwise a database writer could recompute the tail with plain SHA-256.
- **Rotation.** `AUDIT_CHAIN_KEYS_OLD` lists retired keys for verification only. Keys may only move forward along a chain; an older key after a newer one is `retired_key`. A key the verifier does not have is `unknown_key`, never assumed fine.
- **Losing the key** makes its links unverifiable. The setting is off by default and documented with that warning.
- **Single image.** A key generated into `/data/secrets.env` sits next to the database and does not protect against someone holding the whole volume. If it is wired into `infra/allinone/`, update `redu.md`, the README single-container section and `wiki/Self-Hosting.md` in the same PR.
- **ZizkaDB Cloud** should set a key held outside the database host.

### H5. Store the agent's head

- Add `chain_head_seq` and `chain_head_hash` to `agents`, updated in the write that already updates `event_count`, so it costs no extra row lock.
- The verifier reports `head_mismatch` when the last link is not the stored head. That catches a tail deleted without also editing the head. The head is unkeyed, so a full rollback is still caught only by an anchor.

### Fixes this plan depends on, already filed

- #305: agent delete returns a 500 if the agent writes an event at the same moment.
- #306: account delete can deadlock with a concurrent write.
- #307: `checksum_v2_cutoff` is taken from `MAX(sequence_no)` and can be recorded below an in-flight write. The chain relies on that cutoff for legacy checksums.
- #223: forget and agent delete of an event with causal children. Children keep the erased parent's id, so their checksum and link stay valid.

---

## Consequences

**What the chain detects.** "DB writer" means anyone who can change rows in the database. "Anchor" means a `chain_head` the caller stored outside ZizkaDB.

| Change by a DB writer | Today (#310) | After H1–H3, no key | After H1–H5, with key |
|---|---|---|---|
| Edit of a chained event, later links left alone | ✔ | ✔ | ✔ |
| Edit just before a tombstone, own link recomputed | ✘ | ✔ | ✔ |
| Deleted event in the middle | ✔ | ✔ | ✔ |
| Deletion dressed up as an erasure | ✘ | ✘ a forged receipt passes | ✔ |
| Fabricated event appended at the end | ✘ | ✘ | ✔ |
| Deleted newest events, head left alone | only with an anchor | only with an anchor | ✔ `head_mismatch` |
| Deleted newest events, head rolled back | only with an anchor | only with an anchor | only with an anchor |
| Full rewrite of an agent's chain | only with an anchor | only with an anchor | ✔ |
| Row added without a link after the chain started | ✔ | ✔ | ✔ |
| Backdated row among events from before the chain | ✘ | ✘ | ✘ |
| Reordering across agents | ✘ | ✘ | ✘ |

Without a key, the chain protects against accidental and partial changes, and against anyone who does not recompute it. It does not protect against a deliberate database writer. The README and trust pages must say exactly that.

**Better:** the hardening is additive, so no data migration, route change or SDK break is needed. Per-agent chains keep writers of different agents fully parallel.

**Worse:** every erasure path must write tombstones and receipts, and a path that does not shows up as tampering, which is intended. The receipt step adds the agent row lock to forget, so its lock order becomes an invariant tested in CI. A lost key makes keyed links unverifiable.

---

## Alternatives considered

- **One chain per tenant with a contiguous index** (this ADR's first draft). It covers reordering across agents and makes a deleted row visible as a gap before any hash is checked. It was not adopted because #310 already shipped per-agent chains with SDK methods, and switching means migrating every stored `chain_hash`, a second route and an SDK break. It also serialises all writers in a tenant on one head row; a throwaway prototype on simplified tables measured about 3,950 writes/s without the chain against about 1,130 with it, for 20 writers in one tenant. Revisit if cross-agent ordering becomes a requirement.
- **Hash `data::text` / `metadata::text`** (PR #225). It creates a second canonical form next to checksum v2. #310 hashes the v2 checksum instead.
- **Tombstones without receipts.** What #310 ships. A fake tombstone hides a deletion, which H3 closes with a key.
- **Backfill existing rows into the chain.** It would attest history as of the backfill date while looking like it was attested at write time.
- **External anchoring** (publishing heads to a transparency log or timestamp authority). It would catch a full rewrite and a rolled-back tail with or without a key. Deferred; the anchor parameters are its building block.

---

## Implementation order

1. **H1 and H2** (done, #321): schema column and trigger, `delete_and_tombstone()` from `DELETE … RETURNING` (replacing `tombstone_events()`), verifier change, retention lock order, real-Postgres tests for the reproduced gaps, overlapping erasures, upgrade and downgrade.
2. **H5**: head columns and `head_mismatch`.
3. **H3**: receipts, after #305 and #306 are fixed, with a real-Postgres deadlock test for forget against concurrent writes.
4. **H4**: key option, docs and the single-image rule.
5. **Docs**: README and trust-page wording that matches the table above.

Each step updates `core/db/schema.sql`, `init_db()` with idempotent DDL, `dashboard/lib/api.ts` types and the knowledge base (§17.3 response shape, §21 schema) in the same PR, per CLAUDE.md. PR #225 is superseded by #310 and this plan.
