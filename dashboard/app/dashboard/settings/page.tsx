"use client";

import { ApiKeyUsage } from "@/components/ApiKeyUsage";
import { AgentKeysSection } from "@/components/dashboard/AgentKeysSection";
import { useApiKeyQuota } from "@/hooks/useApiKeyQuota";
import { useEdition } from "@/hooks/useEdition";
import { useCopyToClipboard } from "@/hooks/useCopyToClipboard";
import {
  createApiKey,
  deleteManagedAccount,
  getAccountOptions,
  getApiKeys,
  getEmbeddingCatalog,
  getEmbeddingSettings,
  grantRetentionTrial,
  revokeApiKey,
  sendTestEvent,
  updateEmbeddingSettings,
  type AccountOptions,
  type ApiKey,
  type EmbeddingCatalogEntry,
} from "@/lib/api";
import { clearToken, requireAuth } from "@/lib/auth";
import { clearSignupSession } from "@/lib/signup-funnel";
import { AlertTriangle, Check, Copy, Key, Plus, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

export default function SettingsPage() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [embLoading, setEmbLoading] = useState(true);
  const [embSaving, setEmbSaving] = useState(false);
  const [embMsg, setEmbMsg] = useState("");
  const [embErr, setEmbErr] = useState("");
  const [catalog, setCatalog] = useState<EmbeddingCatalogEntry[]>([]);
  const [embProvider, setEmbProvider] = useState("openai");
  const [embModel, setEmbModel] = useState("text-embedding-3-small");
  const [usePlatformKey, setUsePlatformKey] = useState(true);
  const [customApiKey, setCustomApiKey] = useState("");
  const [embReady, setEmbReady] = useState(false);
  const [testBusy, setTestBusy] = useState(false);
  const [testMsg, setTestMsg] = useState("");
  const [testErr, setTestErr] = useState("");
  const [revokingId, setRevokingId] = useState<string | null>(null);
  const [tenantKeyName, setTenantKeyName] = useState("");
  const [tenantKeyCreating, setTenantKeyCreating] = useState(false);
  const [tenantNewKey, setTenantNewKey] = useState<string | null>(null);
  const { copied, copy } = useCopyToClipboard();
  const router = useRouter();
  const [accountOpts, setAccountOpts] = useState<AccountOptions | null>(null);
  const [accountOptsErr, setAccountOptsErr] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState("");
  const [accountBusy, setAccountBusy] = useState(false);
  const [accountMsg, setAccountMsg] = useState("");
  const [accountErr, setAccountErr] = useState("");
  const quota = useApiKeyQuota();
  // Self-hosted (OSS) has no Zizka platform, so it always brings its own
  // embedding key and has no tenant-wide key. Pro/Team keep both.
  const { edition } = useEdition();
  const isOss = edition === "oss";

  useEffect(() => {
    let token: string;
    try {
      token = requireAuth();
    } catch {
      setLoading(false);
      setEmbLoading(false);
      return;
    }
    getApiKeys(token)
      .then(setKeys)
      .finally(() => setLoading(false));
    getEmbeddingCatalog()
      .then((c) => setCatalog(c.providers ?? []))
      .catch(() => setEmbErr("Could not load embedding provider catalog"));
    getEmbeddingSettings(token)
      .then((s) => {
        if (s.provider) setEmbProvider(s.provider);
        if (s.model) setEmbModel(s.model);
        if (typeof s.use_platform_key === "boolean")
          setUsePlatformKey(s.use_platform_key);
        setEmbReady(!!s.ready);
      })
      .catch(() => setEmbErr("Could not load embedding settings"))
      .finally(() => setEmbLoading(false));
    getAccountOptions(token)
      .then(setAccountOpts)
      .catch(() => setAccountOptsErr("Could not load account options"));
  }, []);

  async function handleRevoke(key: ApiKey) {
    const label = key.name ?? key.prefix;
    if (
      !window.confirm(
        `Revoke "${label}"? Apps using this key will stop working immediately.`,
      )
    ) {
      return;
    }
    setRevokingId(key.key_id);
    try {
      const token = requireAuth();
      await revokeApiKey(token, key.key_id);
      setKeys((prev) => prev.filter((k) => k.key_id !== key.key_id));
      await quota.refresh();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to revoke key");
    } finally {
      setRevokingId(null);
    }
  }

  return (
    <div className="max-w-2xl">
      <h1 className="font-semibold text-lg mb-1" style={{ color: "#ffffff" }}>
        Settings
      </h1>
      <p className="text-sm mb-5" style={{ color: "#9aa7b8" }}>
        Embeddings, API keys, and account.
      </p>
      <p
        className="text-xs mb-8 rounded-lg px-3 py-2.5"
        style={{
          color: "#9aa7b8",
          background: "#161c26",
          border: "1px solid #2a3340",
        }}
      >
        {isOss ? (
          <>
            API keys belong to an agent. Your agent is created automatically the
            first time it logs an event (through the REST API, an SDK, MCP or a
            framework integration); create its key in the
            section below.
          </>
        ) : (
          <>
            API keys belong to an agent.{" "}
            <Link href="/dashboard/fleet" className="underline" style={{ color: "#22c55e" }}>
              Create an agent
            </Link>{" "}
            to get a key, or open an agent to add more keys. Legacy tenant-wide
            keys (no agent) still work.
          </>
        )}
      </p>

      {/* Embeddings */}
      <div
        className="rounded-xl p-5 mb-6"
        style={{ background: "#161c26", border: "1px solid #2a3340" }}
      >
        <h2 className="text-sm font-medium text-white mb-1">Embeddings</h2>
        <p className="text-xs mb-4" style={{ color: "#9aa7b8" }}>
          {isOss
            ? "Self-hosted installs do not index vectors by default. Set EMBEDDINGS_ENABLED=true in infra/.env, restart the API, then add your OpenAI API key here to enable semantic search and context injection (OpenAI text-embedding-3-small)."
            : "Choose the model used for semantic search and context injection (like Pinecone's embedding choice). All models use 1536 dimensions. New events use this model; existing vectors are not re-indexed automatically."}
        </p>
        {embLoading ? (
          <div
            className="h-20 rounded animate-pulse"
            style={{ background: "#1f2733" }}
          />
        ) : (
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              setEmbSaving(true);
              setEmbErr("");
              setEmbMsg("");
              try {
                const token = requireAuth();
                // OSS never uses the platform key — always send the user's own.
                const platformKey = isOss ? false : usePlatformKey;
                await updateEmbeddingSettings(token, {
                  provider: embProvider,
                  model: embModel,
                  use_platform_key: platformKey,
                  api_key: platformKey ? undefined : customApiKey,
                });
                setEmbMsg("Saved. New events will use this embedding model.");
                setEmbReady(true);
                setCustomApiKey("");
              } catch (err) {
                setEmbErr(err instanceof Error ? err.message : "Save failed");
              } finally {
                setEmbSaving(false);
              }
            }}
          >
            {/* Model picker is managed-cloud only; OSS uses OpenAI
                text-embedding-3-small (1536-dim) with the user's own key. */}
            {!isOss && (
              <>
                <label className="block text-xs mb-1" style={{ color: "#e8edf5" }}>
                  Model
                </label>
                <select
                  value={embModel}
                  onChange={(e) => setEmbModel(e.target.value)}
                  className="w-full rounded-lg px-3 py-2 text-sm text-white mb-4 outline-none"
                  style={{ background: "#1f2733", border: "1px solid #3a4453" }}
                >
                  {(catalog.find((p) => p.id === embProvider)?.models ?? []).map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.label}
                    </option>
                  ))}
                </select>
              </>
            )}

            {/* Platform-key option is managed-cloud only; OSS always brings its own key. */}
            {!isOss && (
              <label className="flex items-center gap-2 text-sm text-white mb-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={usePlatformKey}
                  onChange={(e) => setUsePlatformKey(e.target.checked)}
                />
                Use Zizka platform embeddings (included on managed cloud)
              </label>
            )}

            {(isOss || !usePlatformKey) && (
              <>
                <label
                  className="block text-xs mb-1"
                  style={{ color: "#e8edf5" }}
                >
                  Your OpenAI API key
                </label>
                <input
                  type="password"
                  value={customApiKey}
                  onChange={(e) => setCustomApiKey(e.target.value)}
                  placeholder="sk-..."
                  className="w-full rounded-lg px-3 py-2 text-sm text-white mb-3 outline-none font-mono"
                  style={{ background: "#1f2733", border: "1px solid #3a4453" }}
                />
              </>
            )}

            <div className="flex items-center gap-3">
              <button
                type="submit"
                disabled={
                  embSaving ||
                  ((isOss || !usePlatformKey) && !customApiKey.trim())
                }
                title={
                  (isOss || !usePlatformKey) && !customApiKey.trim()
                    ? "Enter your OpenAI API key first"
                    : undefined
                }
                className="px-4 py-2 rounded-lg text-sm font-medium text-black disabled:opacity-40 disabled:cursor-not-allowed"
                style={{ background: "#22c55e" }}
              >
                {embSaving ? "Saving…" : "Save embeddings"}
              </button>
              <span
                className="text-xs"
                style={{ color: embReady ? "#22c55e" : "#f97316" }}
              >
                {embReady ? "Configured" : "Not configured"}
              </span>
            </div>
            {embMsg && (
              <p className="text-xs mt-3" style={{ color: "#22c55e" }}>
                {embMsg}
              </p>
            )}
            {embErr && (
              <p className="text-xs mt-3" style={{ color: "#f87171" }}>
                {embErr}
              </p>
            )}
          </form>
        )}
      </div>

      {/* Connection test — managed only; OSS keeps Settings minimal and can use
          the per-agent "Test agent" button below. */}
      {!isOss && (
      <div
        className="rounded-xl p-5 mb-6"
        style={{ background: "#161c26", border: "1px solid #2a3340" }}
      >
        <h2 className="text-sm font-medium text-white mb-1">
          Test event logging
        </h2>
        <p className="text-xs mb-4" style={{ color: "#9aa7b8" }}>
          Logs to agent{" "}
          <span className="font-mono">dashboard-connection-test</span> (not your
          app agent). To test a specific agent, open that agent and click{" "}
          <strong>Test agent</strong>.
        </p>
        <button
          type="button"
          disabled={testBusy}
          onClick={async () => {
            setTestBusy(true);
            setTestErr("");
            setTestMsg("");
            try {
              const token = requireAuth();
              const res = await sendTestEvent(token);
              setTestMsg(res.message ?? `Event ${res.event_id} recorded`);
            } catch (e) {
              setTestErr(e instanceof Error ? e.message : "Test failed");
            } finally {
              setTestBusy(false);
            }
          }}
          className="px-4 py-2 rounded-lg text-sm font-medium text-black disabled:opacity-40"
          style={{ background: "#22c55e" }}
        >
          {testBusy ? "Sending…" : "Send test event"}
        </button>
        {testMsg && (
          <p className="text-xs mt-3" style={{ color: "#22c55e" }}>
            {testMsg}
          </p>
        )}
        {testErr && (
          <p className="text-xs mt-3" style={{ color: "#f87171" }}>
            {testErr}
          </p>
        )}
      </div>
      )}

      {/* Per-agent keys (relocated from the old agent-detail page) */}
      <AgentKeysSection />

      {/* Tenant-wide key — managed only. OSS uses a single per-agent key. */}
      {!isOss && (
      <div
        className="rounded-xl p-5 mb-6"
        style={{ background: "#161c26", border: "1px solid #2a3340" }}
      >
        <h2 className="text-sm font-medium text-white mb-1">
          Tenant-wide API key
        </h2>
        <p className="text-xs mb-3" style={{ color: "#9aa7b8" }}>
          For apps that log to <strong>many agent names</strong> with one key
          (e.g. one key → <span className="font-mono">conv-user1</span>,{" "}
          <span className="font-mono">conv-user2</span>). Most users should
          create per-agent keys in the section above instead.
        </p>
        {!quota.unlimited && <ApiKeyUsage quota={quota} className="mb-3" />}
        {tenantNewKey && (
          <div
            className="rounded-lg p-3 mb-3"
            style={{ background: "#0d2010", border: "1px solid #22c55e40" }}
          >
            <p className="text-xs mb-2" style={{ color: "#22c55e" }}>
              Tenant-wide key — copy now
            </p>
            <div className="flex items-center gap-2">
              <code
                className="flex-1 text-xs font-mono truncate rounded px-2 py-1.5"
                style={{ background: "#0b0f16", color: "#e8edf5" }}
              >
                {tenantNewKey}
              </code>
              <button
                type="button"
                onClick={() => copy(tenantNewKey)}
                className="p-1.5 rounded"
                style={{ background: "#1f2733" }}
              >
                {copied ? (
                  <Check size={14} style={{ color: "#22c55e" }} />
                ) : (
                  <Copy size={14} style={{ color: "#e8edf5" }} />
                )}
              </button>
            </div>
          </div>
        )}
        <form
          className="flex gap-3"
          onSubmit={async (e) => {
            e.preventDefault();
            setTenantKeyCreating(true);
            try {
              const token = requireAuth();
              const res = await createApiKey(
                token,
                tenantKeyName || "tenant-wide",
              );
              setTenantNewKey(res.key);
              setKeys((prev) => [
                {
                  key_id: res.key_id ?? "",
                  prefix: res.prefix,
                  name: res.name,
                  agent_id: null,
                  created_at: new Date().toISOString(),
                  last_used: null,
                },
                ...prev,
              ]);
              setTenantKeyName("");
              await quota.refresh();
            } catch (err) {
              alert(
                err instanceof Error ? err.message : "Failed to create key",
              );
            } finally {
              setTenantKeyCreating(false);
            }
          }}
        >
          <input
            value={tenantKeyName}
            onChange={(e) => setTenantKeyName(e.target.value)}
            placeholder="Key name (e.g. zizka.ai production)"
            disabled={tenantKeyCreating || quota.at_limit}
            className="flex-1 rounded-lg px-3 py-2 text-sm text-white outline-none disabled:opacity-40"
            style={{ background: "#1f2733", border: "1px solid #3a4453" }}
          />
          <button
            type="submit"
            disabled={tenantKeyCreating || quota.at_limit}
            title={
              quota.at_limit
                ? isOss
                  ? "API key limit reached — raise API_KEY_LIMIT_<plan> in your server env"
                  : "API key limit reached — upgrade your plan to create more"
                : undefined
            }
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium text-black disabled:opacity-40 disabled:cursor-not-allowed"
            style={{ background: "#22c55e" }}
          >
            <Plus size={14} />
            {tenantKeyCreating ? "Creating…" : "Create"}
          </button>
        </form>
      </div>
      )}

      {/* All-keys overview — managed only. OSS has a single per-agent key,
          already shown above, so the overview is redundant. */}
      {!isOss && (
      <div
        className="rounded-xl overflow-hidden"
        style={{ border: "1px solid #2a3340" }}
      >
        <div
          className="px-5 py-3 border-b"
          style={{ background: "#161c26", borderColor: "#2a3340" }}
        >
          <span className="text-xs font-medium" style={{ color: "#e8edf5" }}>
            ALL API KEYS (OVERVIEW)
          </span>
        </div>
        {loading ? (
          <div className="p-5 space-y-2">
            {[1, 2].map((i) => (
              <div
                key={i}
                className="h-10 rounded animate-pulse"
                style={{ background: "#1f2733" }}
              />
            ))}
          </div>
        ) : keys.length === 0 ? (
          <div
            className="p-8 text-center text-sm"
            style={{ color: "#e8edf5", background: "#161c26" }}
          >
            No API keys yet. Create an agent on the dashboard to get one.
          </div>
        ) : (
          <div style={{ background: "#161c26" }}>
            {keys.map((key, i) => (
              <div
                key={key.key_id}
                className="flex items-center justify-between px-5 py-4"
                style={{ borderTop: i > 0 ? "1px solid #1f2733" : "none" }}
              >
                <div className="flex items-center gap-3">
                  <Key size={14} style={{ color: "#e8edf5" }} />
                  <div>
                    <div className="text-sm text-white">
                      {key.name ?? "Unnamed"}
                    </div>
                    <div
                      className="text-xs font-mono mt-0.5"
                      style={{ color: "#e8edf5" }}
                    >
                      {key.prefix}...
                      {key.agent_id ? (
                        <span className="ml-2" style={{ color: "#e8edf5" }}>
                          · agent: {key.agent_id}
                        </span>
                      ) : (
                        <span className="ml-2" style={{ color: "#e8edf5" }}>
                          · Unassigned
                        </span>
                      )}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <div className="text-xs" style={{ color: "#e8edf5" }}>
                    {key.last_used
                      ? `Last used ${new Date(key.last_used).toLocaleDateString()}`
                      : "Never used"}
                  </div>
                  <button
                    type="button"
                    disabled={!key.key_id || revokingId === key.key_id}
                    onClick={() => handleRevoke(key)}
                    className="p-1.5 rounded-lg transition disabled:opacity-40"
                    style={{ background: "#1f2733" }}
                    title="Revoke key"
                  >
                    <Trash2 size={14} style={{ color: "#f87171" }} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      )}

      {accountOptsErr && (
        <p className="text-xs mt-4" style={{ color: '#f87171' }}>
          {accountOptsErr}
        </p>
      )}

      {accountOpts?.managed_cloud && (
        <div
          className="rounded-xl p-5 mt-6"
          style={{ background: "#161c26", border: "1px solid #7f1d1d55" }}
        >
          <div className="flex items-start gap-3 mb-3">
            <AlertTriangle
              size={18}
              style={{ color: "#f87171", marginTop: 2 }}
            />
            <div>
              <h2 className="text-sm font-medium text-white mb-1">
                Delete account
              </h2>
              <p
                className="text-xs"
                style={{ color: "#e8edf5", lineHeight: 1.6 }}
              >
                Managed cloud only. This permanently removes your account,
                agents, events, and API keys. Self-hosted / open-source users
                manage data on their own infrastructure.
              </p>
            </div>
          </div>
          {accountMsg && (
            <p className="text-xs mb-3" style={{ color: "#22c55e" }}>
              {accountMsg}
            </p>
          )}
          {accountErr && (
            <p className="text-xs mb-3" style={{ color: "#f87171" }}>
              {accountErr}
            </p>
          )}
          <button
            type="button"
            onClick={() => {
              setDeleteOpen(true);
              setDeleteConfirm("");
              setAccountErr("");
              setAccountMsg("");
            }}
            className="px-4 py-2 rounded-lg text-sm font-medium text-white"
            style={{ background: "#7f1d1d", border: "1px solid #991b1b" }}
          >
            Delete account…
          </button>
        </div>
      )}

      {deleteOpen && accountOpts?.managed_cloud && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.65)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 50,
            padding: 16,
          }}
          onClick={() => !accountBusy && setDeleteOpen(false)}
        >
          <div
            className="rounded-xl p-6"
            style={{
              background: "#161c26",
              border: "1px solid #3a4453",
              maxWidth: 440,
              width: "100%",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-white font-semibold text-base">
                Before you go
              </h3>
              <button
                type="button"
                onClick={() => !accountBusy && setDeleteOpen(false)}
                style={{ color: "#7c8798" }}
              >
                <X size={18} />
              </button>
            </div>

            {accountOpts.retention_trial_available && (
              <div
                className="rounded-lg p-4 mb-4"
                style={{ background: "#0d2010", border: "1px solid #22c55e40" }}
              >
                <p className="text-sm text-white font-medium mb-1">
                  Get {accountOpts.retention_trial_days ?? 30} more days free
                </p>
                <p
                  className="text-xs mb-3"
                  style={{ color: "#e8edf5", lineHeight: 1.6 }}
                >
                  Stay on ZizkaDB cloud with one extra month on your trial — no
                  charge today. One-time offer when deleting your account.
                </p>
                <button
                  type="button"
                  disabled={accountBusy}
                  onClick={async () => {
                    setAccountBusy(true);
                    setAccountErr("");
                    try {
                      const token = requireAuth();
                      const res = await grantRetentionTrial(token);
                      setAccountMsg(res.message);
                      setAccountOpts((prev) =>
                        prev
                          ? {
                              ...prev,
                              retention_trial_available: false,
                              trial_ends_at: res.trial_ends_at,
                            }
                          : prev,
                      );
                      setDeleteOpen(false);
                    } catch (e) {
                      setAccountErr(
                        e instanceof Error
                          ? e.message
                          : "Could not extend trial",
                      );
                    } finally {
                      setAccountBusy(false);
                    }
                  }}
                  className="w-full px-4 py-2 rounded-lg text-sm font-medium text-black disabled:opacity-40"
                  style={{ background: "#22c55e" }}
                >
                  {accountBusy
                    ? "Applying…"
                    : `Keep account — ${accountOpts.retention_trial_days ?? 30}-day extension`}
                </button>
              </div>
            )}

            <p
              className="text-xs mb-3"
              style={{ color: "#e8edf5", lineHeight: 1.6 }}
            >
              Or delete permanently. Type{" "}
              <strong style={{ color: "#fff" }}>DELETE</strong> to confirm.
            </p>
            <input
              value={deleteConfirm}
              onChange={(e) => setDeleteConfirm(e.target.value)}
              placeholder="DELETE"
              className="w-full rounded-lg px-3 py-2 text-sm text-white mb-3 outline-none font-mono"
              style={{ background: "#1f2733", border: "1px solid #3a4453" }}
            />
            <button
              type="button"
              disabled={accountBusy || deleteConfirm !== "DELETE"}
              onClick={async () => {
                setAccountBusy(true);
                setAccountErr("");
                try {
                  const token = requireAuth();
                  await deleteManagedAccount(token);
                  clearToken();
                  clearSignupSession();
                  router.replace("/login?deleted=1");
                } catch (e) {
                  setAccountErr(
                    e instanceof Error ? e.message : "Could not delete account",
                  );
                } finally {
                  setAccountBusy(false);
                }
              }}
              className="w-full px-4 py-2 rounded-lg text-sm font-medium text-white disabled:opacity-40"
              style={{ background: "#991b1b" }}
            >
              {accountBusy ? "Deleting…" : "Delete my account permanently"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
