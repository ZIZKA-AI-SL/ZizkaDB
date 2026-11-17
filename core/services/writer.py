"""
Who wrote an event: the credential behind the request, sealed into the event.

The write path stores it as ``metadata.written_by``, which the event checksum
and the agent's hash chain already cover, so the record of who wrote an event
is as tamper-evident as the event itself. The server always sets it: a client
cannot supply or change it.

- ``{"kind": "api_key", "key_id": ..., "key_prefix": ...}``: an API key. An
  agent-scoped key can only write its own agent, so this names the agent's own
  credential (or, for a key not yet bound, the key that just claimed it).
- ``{"kind": "dashboard", "user_id": ...}``: a signed-in dashboard user. A
  dashboard session can write under any agent's name, so an audit can tell
  these apart from the agent's own writes.
- ``{"kind": "dev_key"}``: the local development key (ENV=development only).
"""

from __future__ import annotations

WRITTEN_BY = "written_by"
# On import, the exporting server's writer is kept here; ``written_by`` names
# whoever ran the import.
WRITTEN_BY_ORIGINAL = "written_by_original"


def writer_of(tenant: dict) -> dict:
    """The ``written_by`` record for the credential behind a request."""
    if tenant.get("key_id"):
        out = {"kind": "api_key", "key_id": str(tenant["key_id"])}
        if tenant.get("key_prefix"):
            out["key_prefix"] = tenant["key_prefix"]
        return out
    if tenant.get("auth") == "dev_key":
        return {"kind": "dev_key"}
    if tenant.get("user_id"):
        return {"kind": "dashboard", "user_id": str(tenant["user_id"])}
    return {"kind": "unknown"}


def seal_writer(metadata: dict | None, written_by: dict | None, *, keep_original: bool = False) -> dict | None:
    """Return metadata with the server's ``written_by`` in place of any client value.

    ``keep_original`` (imports) moves an incoming ``written_by`` to
    ``written_by_original`` instead of dropping it.
    """
    if written_by is None:
        return metadata
    out = dict(metadata or {})
    incoming = out.pop(WRITTEN_BY, None)
    if keep_original and incoming is not None and WRITTEN_BY_ORIGINAL not in out:
        out[WRITTEN_BY_ORIGINAL] = incoming
    elif not keep_original:
        out.pop(WRITTEN_BY_ORIGINAL, None)
    out[WRITTEN_BY] = written_by
    return out
