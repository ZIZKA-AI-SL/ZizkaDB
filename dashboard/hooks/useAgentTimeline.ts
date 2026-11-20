'use client'

import { useEffect, useState } from 'react'
import {
  getAgentTimeline,
  getEvents,
  getSessionTimeline,
  getWhyChain,
  type AgentEvent,
  type AgentTimeline,
  type WhyChain,
} from '@/lib/api'
import { getToken } from '@/lib/auth'
import { groupQuery, type TimeRange, type TimelineGroup } from '@/lib/timeline'

export interface AgentTimelineState {
  timeline: AgentTimeline | null
  loading: boolean
  error: string | null
}

/**
 * The timeline for one agent over a window. A response for an earlier window
 * or agent never overwrites a newer one. The previous timeline stays visible
 * while the next loads, so moving the window doesn't flash.
 */
export function useAgentTimeline(agentId: string | null, range: TimeRange | null): AgentTimelineState {
  const key = agentId && range ? `${agentId}|${range.from}|${range.to}` : null
  const [result, setResult] = useState<{ key: string; timeline: AgentTimeline | null; error: string | null } | null>(null)

  useEffect(() => {
    if (!key || !agentId || !range) return
    const token = getToken()
    if (!token) return
    let cancelled = false
    getAgentTimeline(token, agentId, new Date(range.from).toISOString(), new Date(range.to).toISOString())
      .then((timeline) => {
        if (!cancelled) setResult({ key, timeline, error: null })
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setResult((prev) => ({
            key,
            timeline: prev?.timeline ?? null,
            error: e instanceof Error ? e.message : 'Failed to load the timeline',
          }))
        }
      })
    return () => {
      cancelled = true
    }
    // range is fully described by key
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  if (!key) return { timeline: null, loading: false, error: null }
  return {
    timeline: result?.timeline ?? null,
    loading: result?.key !== key,
    error: result?.key === key ? result.error : null,
  }
}

export interface TimelineEventDetail {
  /** Root first, the selected event last (with its full data). */
  chain: WhyChain | null
  /** Every event of the selected event's chat, oldest first. */
  chat: AgentEvent[] | null
  loading: boolean
  error: string | null
}

/** Everything recorded about one event: its causal chain and its chat. */
export function useTimelineEventDetail(eventId: string | null, sessionId: string | null): TimelineEventDetail {
  const [result, setResult] = useState<{
    eventId: string
    chain: WhyChain | null
    chat: AgentEvent[] | null
    error: string | null
  } | null>(null)

  useEffect(() => {
    if (!eventId) return
    const token = getToken()
    if (!token) return
    let cancelled = false
    Promise.all([
      getWhyChain(token, eventId),
      sessionId ? getSessionTimeline(token, sessionId).then((s) => s.events) : Promise.resolve(null),
    ])
      .then(([chain, chat]) => {
        if (!cancelled) setResult({ eventId, chain, chat, error: null })
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setResult({ eventId, chain: null, chat: null, error: e instanceof Error ? e.message : 'Failed to load the event' })
        }
      })
    return () => {
      cancelled = true
    }
  }, [eventId, sessionId])

  const current = eventId && result?.eventId === eventId ? result : null
  return {
    chain: current?.chain ?? null,
    chat: current?.chat ?? null,
    loading: !!eventId && !current,
    error: current?.error ?? null,
  }
}

export interface GroupEventsState {
  events: AgentEvent[] | null
  loading: boolean
  error: string | null
}

/**
 * The events behind a grouped dot (one type, one time slot), up to ``limit``.
 * A response for an earlier group or limit never overwrites a newer one; the
 * loaded events stay visible while more load.
 */
export function useGroupEvents(agentId: string, group: TimelineGroup | null, limit: number): GroupEventsState {
  const key = group ? `${agentId}|${group.type}|${group.from}|${group.to}|${limit}` : null
  const groupKey = group ? `${agentId}|${group.type}|${group.from}|${group.to}` : null
  const [result, setResult] = useState<{ key: string; groupKey: string; events: AgentEvent[] | null; error: string | null } | null>(null)

  useEffect(() => {
    if (!key || !groupKey || !group) return
    const token = getToken()
    if (!token) return
    let cancelled = false
    getEvents(token, agentId, groupQuery(group, limit))
      .then((events) => {
        if (!cancelled) setResult({ key, groupKey, events, error: null })
      })
      .catch((e: unknown) => {
        if (!cancelled) setResult({ key, groupKey, events: null, error: e instanceof Error ? e.message : 'Failed to load the events' })
      })
    return () => {
      cancelled = true
    }
    // group and limit are fully described by key
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  if (!key) return { events: null, loading: false, error: null }
  const sameGroup = result?.groupKey === groupKey
  return {
    events: sameGroup ? result!.events : null,
    loading: result?.key !== key,
    error: result?.key === key ? result.error : null,
  }
}
