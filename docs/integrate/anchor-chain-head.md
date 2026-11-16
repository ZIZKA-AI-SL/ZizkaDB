# Anchor the chain head outside ZizkaDB

`GET /v1/events/chain` (`verify_chain()` in the SDKs) proves that nothing in an agent's history was edited, removed or reordered, **as long as the chain's newest link is still the one the server recorded**. Someone with write access to the database can delete the newest events and also move the recorded head back. After that the chain is shorter but consistent, and a full check returns `intact: true`.

Only a copy of the head kept outside ZizkaDB catches that. [ADR-009](../adr/009-audit-hash-chain.md) explains why. This page shows how to keep that copy so that the person who controls the database can't quietly replace it.

## What it proves, and what it doesn't

- **Proves:** every event up to the anchored head is still there and unchanged, and the anchor itself existed at the time it was stamped.
- **Doesn't prove:** anything about events written after the newest anchor. Anchor regularly; the gap between anchors is what's left open.
- **Doesn't replace a key.** With [ADR-009 H4](../adr/009-audit-hash-chain.md#h4-optional-key-held-outside-the-database)'s key, a database writer can't rebuild the chain at all; an anchor shows that a rebuild happened. They close different holes.
- **Sends nothing private.** The anchor holds the agent name, a sequence number and a SHA-256 link. With a timestamp authority, only the SHA-256 digest of that record leaves your machine.

## Two rules for the copy

1. **It must be stamped.** A stored head on its own can be rewritten together with the database. An [RFC 3161](https://www.rfc-editor.org/rfc/rfc3161) timestamp from a timestamp authority (TSA) signs the head's digest and the time, so no one can create a matching head for an earlier date afterwards.
2. **Someone other than the database operator should keep a copy.** A stamp can't be forged, but the files can be deleted. Send each anchor to a party who doesn't control the database: your auditor, your compliance team, or a write-once bucket in a separate account (for example S3 Object Lock in compliance mode).

## 1. Anchor the head

Run this on a schedule, for example once a day per agent, and after every audit:

```bash
export ZIZKADB_URL=https://db.zizka.ai          # or your server, e.g. http://localhost:8000
export ZIZKADB_API_KEY=zizkadb_live_...
AGENT=support-bot

# Check the chain and keep the head it returns.
curl -s -H "Authorization: Bearer $ZIZKADB_API_KEY" \
  "$ZIZKADB_URL/v1/events/chain?agent=$AGENT" > check.json

python3 - "$AGENT" <<'EOF' > head.json
import datetime, json, sys
check = json.load(open("check.json"))
assert check["intact"], check["problems"]   # anchor only a chain that verifies
head = check["chain_head"]
print(json.dumps({
    "agent": sys.argv[1],
    "sequence_no": head["sequence_no"],
    "chain_hash": head["chain_hash"],
    "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
}, separators=(",", ":")))
EOF

# Timestamp it. Only the SHA-256 digest of head.json is sent.
openssl ts -query -data head.json -sha256 -cert -out head.tsq
curl -s -H "Content-Type: application/timestamp-query" \
  --data-binary @head.tsq https://freetsa.org/tsr -o head.tsr
```

Keep `head.json` and `head.tsr` together, and send copies to the other party (rule 2). Name them by agent and date, for example `support-bot-2026-11-16.json`.

**Choosing a TSA.** [FreeTSA](https://freetsa.org) is used above because it needs no account. For production, use a TSA you'd rely on in front of an auditor. In the EU, a **qualified** TSA from the [EU Trusted List](https://eidas.ec.europa.eu/efda/trust-services/browse/eidas/tls) gives the timestamp a legal presumption of accurate date and time and data integrity (eIDAS, Article 41). Any RFC 3161 TSA works with the same commands; change the URL and the certificates.

## 2. Check later from the anchor

An auditor, or a scheduled job, first checks the stamp, then asks ZizkaDB to check the chain from the anchored head:

```bash
# The stamp: head.json existed, unchanged, at the stamped time.
curl -s -o cacert.pem https://freetsa.org/files/cacert.pem
curl -s -o tsa.crt https://freetsa.org/files/tsa.crt
openssl ts -verify -data head.json -in head.tsr -CAfile cacert.pem -untrusted tsa.crt
openssl ts -reply -in head.tsr -text | grep "Time stamp"

# The chain: everything up to the anchored head is still there, and newer events link to it.
SEQ=$(python3 -c "import json; print(json.load(open('head.json'))['sequence_no'])")
HASH=$(python3 -c "import json; print(json.load(open('head.json'))['chain_hash'])")
curl -s -H "Authorization: Bearer $ZIZKADB_API_KEY" \
  "$ZIZKADB_URL/v1/events/chain?agent=$AGENT&after_seq=$SEQ&anchor=$HASH"
```

The same check from the SDKs:

```python
check = await db.verify_chain("support-bot", after_seq=seq, anchor=chain_hash)
```

```ts
const check = await db.verifyChain('support-bot', { sequenceNo: seq, chainHash })
```

`openssl ts -verify` should print `Verification: OK`. A warning that `tsa.crt` "is not a CA cert" is expected: that file is the TSA's signing certificate, not a CA.

### Reading the result

- **`intact: true`:** nothing up to the anchored head was removed or rewritten, and every newer event links back to it. `chain_head` is the new newest link; anchor it next. It is `null` when nothing was written since the anchor, so keep using the old one.
- **`anchor_mismatch`:** no event or tombstone at the anchored sequence number has the anchored hash. The anchored event was deleted outside ZizkaDB, or the chain was rewritten. Lawful deletions don't cause this: `forget()` and retention keep a tombstone with the same link.
- **Any other problem:** see the problem list in [ADR-009](../adr/009-audit-hash-chain.md).

## What this catches: tested

Against a ZizkaDB server, the commands above were run on an agent with five events. Then someone with database access deleted the newest three, including the anchored one, and moved the recorded head back:

| Check | Result |
|---|---|
| Full check, no anchor | `intact: true`: the shortened chain is consistent |
| Check from the stamped anchor | `intact: false`, `anchor_mismatch` at the anchored sequence number |

## Other places to anchor

A timestamp authority is the simplest option with `openssl` and `curl`. Any of these also works, as long as the person who controls the database can't change or remove what was stored:

- **A public transparency log**, for example [Sigstore Rekor](https://docs.sigstore.dev/logging/overview/) through `cosign sign-blob head.json`. Entries are public and can't be removed.
- **A write-once bucket in a separate account**, for example S3 Object Lock in compliance mode, owned by someone other than the database operator.
- **Your auditor's own records**: send `head.json` by a channel they archive, such as signed email or their document system.
