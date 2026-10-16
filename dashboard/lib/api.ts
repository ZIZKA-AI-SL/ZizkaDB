// NEXT_PUBLIC_ prefix makes this available in the browser.
// Falls back to '' (relative URL) so Nginx can route /v1/ → FastAPI.
export const API = process.env.NEXT_PUBLIC_API_URL ?? ''

export class AuthRequestError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'AuthRequestError'
    this.status = status
  }
}

function formatApiError(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map((d) => (typeof d === 'object' && d && 'msg' in d ? String((d as { msg: string }).msg) : String(d))).join('; ')
  }
  if (detail && typeof detail === 'object' && 'msg' in detail) return String((detail as { msg: string }).msg)
  return fallback
}

// Default `T = any` preserves the exact pre-existing untyped behavior for any
// call site that doesn't opt into an explicit type — this is additive only.
export const API_FETCH_TIMEOUT_MS = 30_000

async function apiFetch<T = any>(path: string, token: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((options.headers as Record<string, string>) || {}),
  }
  if (token) {
    headers.Authorization = `Bearer ${token}`
  }

  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), API_FETCH_TIMEOUT_MS)

  let res: Response
  try {
    res = await fetch(`${API}${path}`, {
      ...options,
      headers,
      signal: options.signal ?? controller.signal,
    })
  } catch (e) {
    if (e instanceof Error && e.name === 'AbortError') {
      throw new Error('Request timed out. Check your connection and try again.')
    }
    throw e
  } finally {
    clearTimeout(timeout)
  }

  if (res.status === 401) {
    if (typeof window !== 'undefined') {
      const { clearToken } = await import('./auth')
      clearToken()
      window.location.href = '/login'
    }
    throw new Error('Session expired. Please sign in again.')
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(formatApiError(err.detail, res.statusText || 'API error'))
  }
  return res.json()
}

// ─────────────────────────────────────────────────────────────────────────────
// Agents
// ─────────────────────────────────────────────────────────────────────────────

export interface Agent {
  agent: string
  first_seen: string
  last_seen: string
  event_count: number
  api_key_count?: number
}

export interface CreateAgentResponse {
  agent?: string
  api_key?: { key: string; key_id?: string; prefix?: string }
  message?: string
}

export async function getAgents(token: string): Promise<Agent[]> {
  return apiFetch('/v1/agents', token)
}

export async function createAgent(token: string, agentId: string, keyName?: string): Promise<CreateAgentResponse> {
  return apiFetch('/v1/agents', token, {
    method: 'POST',
    body: JSON.stringify({ agent_id: agentId, key_name: keyName }),
  })
}

export async function deleteAgent(token: string, agentId: string): Promise<{ message?: string }> {
  return apiFetch(`/v1/agents/${encodeURIComponent(agentId)}`, token, {
    method: 'DELETE',
  })
}

// ─────────────────────────────────────────────────────────────────────────────
// Agent-scoped API keys
// ─────────────────────────────────────────────────────────────────────────────

export interface ApiKey {
  key_id: string
  prefix: string
  name: string | null
  agent_id: string | null
  created_at: string
  last_used: string | null
}

export interface CreateKeyResponse {
  key: string
  key_id?: string
  prefix: string
  name: string | null
}

export async function getAgentApiKeys(token: string, agentId: string): Promise<ApiKey[]> {
  return apiFetch(`/v1/agents/${encodeURIComponent(agentId)}/api-keys`, token)
}

export async function createAgentApiKey(token: string, agentId: string, name?: string): Promise<CreateKeyResponse> {
  return apiFetch(`/v1/agents/${encodeURIComponent(agentId)}/api-keys`, token, {
    method: 'POST',
    body: JSON.stringify({ name }),
  })
}

export async function revokeAgentApiKey(token: string, agentId: string, keyId: string): Promise<{ message?: string }> {
  return apiFetch(
    `/v1/agents/${encodeURIComponent(agentId)}/api-keys/${encodeURIComponent(keyId)}`,
    token,
    { method: 'DELETE' },
  )
}

export async function sendTestEvent(token: string): Promise<{ message?: string; event_id?: string }> {
  return apiFetch('/v1/auth/test-event', token, { method: 'POST' })
}

export async function sendAgentTestEvent(token: string, agentId: string): Promise<{ message?: string }> {
  return apiFetch(`/v1/agents/${encodeURIComponent(agentId)}/test-event`, token, {
    method: 'POST',
  })
}

// ─────────────────────────────────────────────────────────────────────────────
// Events, search, sessions, memory, time-travel
// ─────────────────────────────────────────────────────────────────────────────

export interface AgentEvent {
  event_id: string
  agent: string
  timestamp: string
  event: string
  data: Record<string, unknown>
  parent_id: string | null
  session_id: string | null
  sequence_no: number
  index_status?: 'pending' | 'indexed' | 'failed' | 'skipped'
}

/** One tool or API an agent called, with its call count and error rate. */
export interface AgentAction {
  kind: 'tool' | 'api'
  name: string
  calls: number
  errors: number
  error_rate_pct: number
  /** Share of all named tool/API calls in scope. */
  share_pct: number
}

export interface AgentStats {
  total_events: number
  unique_event_types: number
  sessions: number
  first_event: string | null
  last_event: string | null
  top_events: { event: string; count: number }[]
  top_actions?: AgentAction[]
}

export interface WhyChain {
  chain_complete?: boolean
  orphan?: boolean
  depth_truncated?: boolean
  scoped_agent_limited?: boolean
  event_id: string
  chain_length: number
  chain: AgentEvent[]
}

export interface ImpactNode extends AgentEvent {
  depth: number
}

export interface ImpactTree {
  event_id: string
  node_count: number
  truncated: boolean
  nodes: ImpactNode[]
}

export interface AgentSession {
  session_id: string
  event_count: number
  event_types: number
  started_at: string
  ended_at: string
  duration_seconds: number
  types: string[]
}

export interface TenantSession {
  session_id: string
  event_count: number
  agent_count: number
  event_types: number
  started_at: string
  ended_at: string
  agents: string[]
}

export interface SessionTimeline {
  session_id: string
  events: AgentEvent[]
}

export async function getAgentStats(token: string, agentId: string): Promise<AgentStats> {
  return apiFetch(`/v1/agents/${encodeURIComponent(agentId)}/stats`, token)
}

export async function getEvents(
  token: string,
  agent: string,
  params: Record<string, string> = {},
): Promise<AgentEvent[]> {
  const qs = new URLSearchParams({ agent, ...params }).toString()
  const data = await apiFetch<AgentEvent[] | { events?: AgentEvent[] }>(`/v1/events?${qs}`, token)
  // API returns either an array directly or { events: [] }
  return Array.isArray(data) ? data : data.events ?? []
}

export async function getWhyChain(token: string, eventId: string): Promise<WhyChain> {
  return apiFetch(`/v1/events/${eventId}/why`, token)
}

// Downstream mirror of getWhyChain: everything an event caused.
export async function getImpactTree(
  token: string,
  eventId: string,
  depth?: number,
): Promise<ImpactTree> {
  const qs = depth !== undefined ? `?depth=${depth}` : ''
  return apiFetch(`/v1/events/${eventId}/impact${qs}`, token)
}

export async function getTenantSessions(
  token: string,
  agent?: string,
): Promise<TenantSession[]> {
  const qs = agent ? `?agent=${encodeURIComponent(agent)}` : ''
  return apiFetch(`/v1/sessions${qs}`, token)
}

export async function getSessionTimeline(
  token: string,
  sessionId: string,
): Promise<SessionTimeline> {
  return apiFetch(`/v1/sessions/${encodeURIComponent(sessionId)}/events`, token)
}

export async function getSessionWhyChain(
  token: string,
  sessionId: string,
  eventId: string,
): Promise<WhyChain> {
  return apiFetch(
    `/v1/sessions/${encodeURIComponent(sessionId)}/why/${encodeURIComponent(eventId)}`,
    token,
  )
}

// The backend's search response shape isn't fully pinned down (some call
// sites defensively handle both `{ results: [...] }` and a bare array) — kept
// loosely typed here rather than forcing a shape that would fight that
// existing, intentional defensiveness.
export async function searchEvents(token: string, query: string, agent?: string) {
  return apiFetch('/v1/search', token, {
    method: 'POST',
    body: JSON.stringify({ query, agent, limit: 20 }),
  })
}

export async function getAgentSessions(token: string, agentId: string): Promise<AgentSession[]> {
  return apiFetch(`/v1/agents/${encodeURIComponent(agentId)}/sessions`, token)
}

export async function getMemoryDiff(token: string, sessionId: string): Promise<Record<string, unknown>> {
  return apiFetch(`/v1/memory/diff/${encodeURIComponent(sessionId)}`, token)
}

export async function timeTravel(token: string, agent: string, timestamp: string): Promise<Record<string, unknown>> {
  const qs = new URLSearchParams({ agent, timestamp }).toString()
  return apiFetch(`/v1/events/at?${qs}`, token)
}

// ─────────────────────────────────────────────────────────────────────────────
// Embeddings
// ─────────────────────────────────────────────────────────────────────────────

export interface EmbeddingCatalogEntry {
  id: string
  models: { id: string; label: string; description?: string }[]
}

export interface EmbeddingSettings {
  provider?: string
  model?: string
  use_platform_key?: boolean
  ready?: boolean
}

export async function getEmbeddingCatalog(): Promise<{ providers?: EmbeddingCatalogEntry[] }> {
  return apiFetch('/v1/settings/embeddings/catalog', '')
}

export async function getEmbeddingSettings(token: string): Promise<EmbeddingSettings> {
  return apiFetch('/v1/settings/embeddings', token)
}

export async function updateEmbeddingSettings(
  token: string,
  body: {
    provider: string
    model: string
    use_platform_key: boolean
    api_key?: string
  },
): Promise<{ message?: string }> {
  return apiFetch('/v1/settings/embeddings', token, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

// ─────────────────────────────────────────────────────────────────────────────
// Baseline / drift
// ─────────────────────────────────────────────────────────────────────────────

export interface BaselineWindow {
  sessions: number
  events: number
  event_distribution: Record<string, number>
  transitions: Record<string, number>
  avg_events_per_session: number
  avg_session_seconds: number
  error_rate_pct: number
  actions?: AgentAction[]
  action_distribution?: Record<string, number>
}

export interface BaselineChange {
  metric: string
  baseline: number
  recent: number
  delta_pp: number
}

export interface AgentBaseline {
  agent: string
  status: 'ok' | 'warming_up' | 'insufficient_data'
  message?: string
  sessions: number
  recent_window: number
  baseline?: BaselineWindow
  recent?: BaselineWindow
  drift?: {
    score: number
    behavior_change_pct: number
    verdict: 'stable' | 'minor_drift' | 'noticeable_drift' | 'significant_drift'
    biggest_changes: BaselineChange[]
    error_rate_change_pp: number
    session_length_change_pct: number
  }
}

export async function getAgentBaseline(
  token: string,
  agentId: string,
  recentWindow = 50,
  window?: '24h' | '7d' | '30d',
): Promise<AgentBaseline> {
  const qs = new URLSearchParams({ recent_window: String(recentWindow) })
  if (window) qs.set('window', window)
  return apiFetch(
    `/v1/agents/${encodeURIComponent(agentId)}/baseline?${qs.toString()}`,
    token,
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Reports — one consolidated, date-ranged per-agent report (see core/services/reports.py)
// ─────────────────────────────────────────────────────────────────────────────

export type ReportGranularity = 'day' | 'week'
export type ReportHealth = 'healthy' | 'attention' | 'idle' | 'drift' | 'error'
export type ReportSeverity = 'critical' | 'warning' | 'info' | 'positive'

export interface ReportSummary {
  total_events: number
  unique_event_types: number
  sessions: number
  active_days: number
  error_count: number
  error_rate_pct: number
  first_event: string | null
  last_event: string | null
  health: ReportHealth
  previous: { total_events: number; sessions: number; error_rate_pct: number }
}

export interface ReportBucket {
  bucket: string
  events: number
  errors: number
  sessions: number
}

export interface ReportTopEvent {
  event_type: string
  count: number
  pct: number
}

export interface ReportSession {
  session_id: string
  event_count: number
  event_types: number
  started_at: string
  ended_at: string
  duration_seconds: number
}

export interface ReportDrift {
  score: number
  verdict: 'stable' | 'minor_drift' | 'noticeable_drift' | 'significant_drift'
  behavior_change_pct: number
  error_rate_change_pp: number
  biggest_changes: BaselineChange[]
}

export interface ReportRecommendation {
  severity: ReportSeverity
  category: string
  title: string
  detail: string
}

export interface ReportPayload {
  agent: string
  generated_at: string
  report_version: string
  period: { from: string; to: string; days: number; granularity: ReportGranularity }
  summary: ReportSummary
  timeseries: ReportBucket[]
  top_events: ReportTopEvent[]
  distribution: {
    event_distribution: Record<string, number>
    transitions: Record<string, number>
  }
  actions?: AgentAction[]
  sessions: ReportSession[]
  drift: ReportDrift | null
  recommendations: ReportRecommendation[]
}

export async function getAgentReport(
  token: string,
  agentId: string,
  params: { from: string; to: string; granularity?: ReportGranularity },
): Promise<ReportPayload> {
  const qs = new URLSearchParams({ from: params.from, to: params.to })
  if (params.granularity) qs.set('granularity', params.granularity)
  return apiFetch(`/v1/agents/${encodeURIComponent(agentId)}/report?${qs.toString()}`, token)
}

// ─────────────────────────────────────────────────────────────────────────────
// Token usage — per-agent token/cost report (see core/services/token_usage.py)
// Reads events.data.token_usage; no schema migration. See docs/adr for the
// JSONB convention SDKs must adopt for data to appear here.
// ─────────────────────────────────────────────────────────────────────────────

// Wider than ReportGranularity: the token-usage endpoint additionally accepts
// 'hour' (for the 24h preset). /report intentionally keeps its narrower set.
export type TokenUsageGranularity = 'hour' | 'day' | 'week'

export interface TokenUsageTotals {
  total_requests: number
  success_count: number
  failed_count: number
  success_rate_pct: number
  total_tokens: number
  input_tokens: number
  output_tokens: number
  cached_tokens: number
  reasoning_tokens: number
  avg_tokens_per_request: number
  total_cost: number
}

export interface TokenUsageBreakdownRow {
  key: string
  tokens: number
  cost: number
  requests: number
}

export type TokenUsageBreakdown = TokenUsageBreakdownRow[]

export interface TokenUsageTrendPoint {
  bucket: string
  input_tokens: number
  output_tokens: number
  tokens: number
  cost: number
  requests: number
}

export interface TokenUsagePayload {
  agent: string
  generated_at: string
  period: { from: string; to: string; granularity: TokenUsageGranularity }
  totals: TokenUsageTotals
  unpriced_models: string[]
  breakdown: {
    model: TokenUsageBreakdown
    agent: TokenUsageBreakdown
    workflow: TokenUsageBreakdown
    tool: TokenUsageBreakdown
    user: TokenUsageBreakdown
  }
  trend: TokenUsageTrendPoint[]
  top_consumers: {
    model: TokenUsageBreakdown
    agent: TokenUsageBreakdown
    workflow: TokenUsageBreakdown
    tool: TokenUsageBreakdown
    user: TokenUsageBreakdown
  }
}

export async function getAgentTokenUsage(
  token: string,
  agentId: string,
  params: { from: string; to: string; granularity?: TokenUsageGranularity },
): Promise<TokenUsagePayload> {
  const qs = new URLSearchParams({ from: params.from, to: params.to })
  if (params.granularity) qs.set('granularity', params.granularity)
  return apiFetch(`/v1/agents/${encodeURIComponent(agentId)}/token-usage?${qs.toString()}`, token)
}

// ─────────────────────────────────────────────────────────────────────────────
// Suggestions — evidence-grounded AI recommendations (see core/services/suggestions)
// ─────────────────────────────────────────────────────────────────────────────

export type SuggestionSeverity = 'critical' | 'high' | 'medium' | 'low' | 'informational'
export type SuggestionCategory =
  | 'general'
  | 'token_optimization'
  | 'error_prevention'
  | 'performance'
  | 'reliability'
  | 'code'
export type SuggestionStatus = 'ok' | 'no_evidence' | 'ai_not_configured'

export interface SuggestionEvidence {
  id: string
  category: SuggestionCategory
  label: string
  summary: string
  metrics: Record<string, unknown>
  samples: string[]
  strength: number
}

export interface Suggestion {
  title: string
  category: SuggestionCategory
  severity: SuggestionSeverity
  confidence: number
  evidence: string[]
  recommendation: string
  expected_impact: string
  code_fix?: { language: string; code: string }
}

export interface SuggestionsResult {
  agent: string
  status: SuggestionStatus
  period: { from: string; to: string; days: number }
  model: string | null
  generated_at: string
  suggestions: Suggestion[]
  evidence: SuggestionEvidence[]
  meta: Record<string, unknown>
}

export async function getAgentSuggestions(
  token: string,
  agentId: string,
  params: { from: string; to: string; refresh?: boolean },
): Promise<SuggestionsResult> {
  const qs = new URLSearchParams({ from: params.from, to: params.to })
  if (params.refresh) qs.set('refresh', '1')
  return apiFetch(`/v1/agents/${encodeURIComponent(agentId)}/suggestions?${qs.toString()}`, token)
}

// ─────────────────────────────────────────────────────────────────────────────
// Token Optimization — deterministic, non-AI cost/token savings suggestions
// (see core/services/token_optimization.py + docs/adr/007). A separate,
// unrelated vocabulary from the AI Suggestions above — this feature never
// calls an LLM, so every number is a real, reproducible computation.
// ─────────────────────────────────────────────────────────────────────────────

export type TokenOptCategory =
  | 'high_consumption'
  | 'model_optimization'
  | 'cache_opportunity'
  | 'retry_analysis'
  | 'cost_anomaly'
export type TokenOptSeverity = 'critical' | 'high' | 'medium' | 'low'
export type TokenOptStatus = 'ok' | 'no_data'

export interface TokenOptimizationSuggestion {
  id: string
  title: string
  category: TokenOptCategory
  severity: TokenOptSeverity
  estimated_monthly_savings_usd: number
  estimated_token_reduction_pct: number
  confidence_score: number
  summary: string
  why: string
  recommended_action: string
  current_state: Record<string, unknown>
  recommended_state: Record<string, unknown>
  affected: { agent?: string | null; workflow?: string | null; model?: string | null; user?: string | null }
  related_report_link: string | null
  sample_size: number
}

export interface TokenOptimizationAggregates {
  total_potential_monthly_savings_usd: number
  potential_token_reduction_pct: number
  cost_reduction_pct: number
  optimization_score: number
  suggestion_count: number
  critical_count: number
}

export interface TokenOptimizationResult {
  agent: string
  status: TokenOptStatus
  period: { from: string; to: string; granularity?: TokenUsageGranularity }
  generated_at: string
  suggestions: TokenOptimizationSuggestion[]
  aggregates: TokenOptimizationAggregates
  unpriced_models: string[]
  meta: Record<string, unknown>
}

export async function getAgentTokenOptimization(
  token: string,
  agentId: string,
  params: { from: string; to: string; granularity?: TokenUsageGranularity },
): Promise<TokenOptimizationResult> {
  const qs = new URLSearchParams({ from: params.from, to: params.to })
  if (params.granularity) qs.set('granularity', params.granularity)
  return apiFetch(`/v1/agents/${encodeURIComponent(agentId)}/token-optimization?${qs.toString()}`, token)
}

export async function getApiKeys(token: string) {
  return apiFetch('/v1/auth/api-keys', token)
}

export async function createApiKey(token: string, name: string): Promise<CreateKeyResponse> {
  return apiFetch('/v1/auth/api-keys', token, {
    method: 'POST',
    body: JSON.stringify({ name }),
  })
}

export async function revokeApiKey(token: string, keyId: string): Promise<{ message?: string }> {
  return apiFetch(`/v1/auth/api-keys/${encodeURIComponent(keyId)}`, token, {
    method: 'DELETE',
  })
}

export interface ApiKeyUsage {
  plan: string | null
  limit: number | null
  used: number
  unlimited: boolean
  at_limit: boolean
}

// Account-wide API key quota for the current plan. The backend is the source of
// truth for the limit, so the UI never hardcodes plan limits.
export async function getApiKeyUsage(token: string): Promise<ApiKeyUsage> {
  return apiFetch('/v1/auth/api-keys/usage', token)
}

export async function requestOtp(email: string, intent: 'signup' | 'login' = 'login') {
  const res = await fetch(`${API}/v1/auth/request-otp`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      email: email.toLowerCase().trim(),
      intent,
    }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new AuthRequestError(
      formatApiError(err.detail, 'Failed to send code'),
      res.status,
    )
  }
  return res.json()
}

export async function verifyOtp(
  email: string,
  otp: string,
  opts?: {
    intent?: 'signup' | 'login'
    gdprConsent?: boolean
    marketingConsent?: boolean
  },
) {
  const intent = opts?.intent ?? 'login'
  const res = await fetch(`${API}/v1/auth/verify-otp`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      email: email.toLowerCase().trim(),
      otp: otp.trim(),
      intent,
      ...(opts?.gdprConsent ? { gdpr_consent: true } : {}),
      ...(opts?.marketingConsent ? { marketing_consent: true } : {}),
    }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new AuthRequestError(
      formatApiError(err.detail, 'Invalid or expired code'),
      res.status,
    )
  }
  const data = await res.json()
  if (!data?.access_token) throw new Error('Sign-in succeeded but no session token was returned')
  return data as {
    access_token: string
    token_type: string
    requires_plan_selection?: boolean
    requires_checkout?: boolean
    has_access?: boolean
    plan?: string | null
  }
}

// Self-host local dev only — issues a token for the default dev tenant.
export async function devLogin(): Promise<{ access_token: string }> {
  const res = await fetch(`${API}/v1/auth/dev-token`, { method: 'POST' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new AuthRequestError(
      formatApiError(err.detail, 'Dev token failed'),
      res.status,
    )
  }
  return res.json()
}

export type SelfHostLoginMode = 'one_click' | 'admin_token' | 'unavailable'

export interface SelfHostConfig {
  self_hosted: boolean
  login: SelfHostLoginMode | null
}

// Public — which sign-in the self-hosted dashboard should show.
export async function getSelfHostConfig(): Promise<SelfHostConfig> {
  const res = await fetch(`${API}/v1/auth/selfhost`)
  if (!res.ok) {
    throw new AuthRequestError(`API returned ${res.status}`, res.status)
  }
  return res.json()
}

// Self-host only — signs in as the instance's single owner tenant.
// `token` is the SELFHOST_ADMIN_TOKEN; omit it in one-click mode.
export async function selfHostLogin(token?: string): Promise<{ access_token: string }> {
  const res = await fetch(`${API}/v1/auth/selfhost-login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(token ? { token } : {}),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new AuthRequestError(
      formatApiError(err.detail, 'Sign-in failed'),
      res.status,
    )
  }
  return res.json()
}

export interface BillingStatus {
  enforced: boolean
  has_access: boolean
  requires_plan_selection: boolean
  requires_checkout: boolean
  subscription_status: string | null
  trial_ends_at: string | null
  plan: string | null
  trial_days?: number | null
}

export async function getBillingStatus(token: string): Promise<BillingStatus> {
  return apiFetch('/v1/billing/status', token)
}

export async function selectBillingPlan(
  token: string,
  plan: 'pro' | 'team',
): Promise<BillingStatus> {
  return apiFetch('/v1/billing/select-plan', token, {
    method: 'POST',
    body: JSON.stringify({ plan }),
  })
}

export interface AccountOptions {
  managed_cloud: boolean
  retention_trial_available?: boolean
  retention_trial_days?: number
  trial_ends_at?: string | null
  email?: string | null
}

export async function getAccountOptions(token: string): Promise<AccountOptions> {
  return apiFetch('/v1/account/options', token)
}

export async function grantRetentionTrial(token: string): Promise<{
  message: string
  trial_ends_at: string
  retention_trial_available: boolean
}> {
  return apiFetch('/v1/account/retention-trial', token, { method: 'POST' })
}

export async function deleteManagedAccount(token: string): Promise<{ message: string }> {
  return apiFetch('/v1/account', token, { method: 'DELETE' })
}
