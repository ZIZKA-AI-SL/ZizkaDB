'use client'

import { useCallback, useState } from 'react'
import { Search, X } from 'lucide-react'
import type { AgentEvent, AgentStats } from '@/lib/api'
import { eventColor } from '@/lib/events'
import { colors, radii } from '@/lib/design-tokens'
import { Button, EmptyState, ErrorState, Skeleton } from '@/components/ui'
import type { AgentEventsState } from '@/hooks/useAgentEvents'
import { useWhyChain } from '@/hooks/useWhyChain'
import { EventList } from './EventList'
import { EventDetailPanel, type PanelTab } from './EventDetailPanel'

function FilterPill({
  label,
  active,
  onClick,
  color,
}: {
  label: string
  active: boolean
  onClick: () => void
  color?: string
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className="text-xs px-2.5 py-1 rounded-full transition inline-flex items-center gap-1.5"
      style={{
        background: active ? colors.surfaceHover : 'transparent',
        color: active ? colors.textStrong : colors.textMuted,
        border: `1px solid ${active ? colors.borderStrong : colors.border}`,
      }}
    >
      {color && (
        <span
          aria-hidden="true"
          className="rounded-full"
          style={{ width: 6, height: 6, background: color }}
        />
      )}
      {label}
    </button>
  )
}

export function EventsSegment({
  events,
  stats,
}: {
  events: AgentEventsState
  stats: AgentStats | null
}) {
  const [selected, setSelected] = useState<AgentEvent | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [panelTab, setPanelTab] = useState<PanelTab>('data')
  const why = useWhyChain()

  const { displayEvents, searchResults } = events

  // Re-clicking the selected event closes the panel.
  const selectEvent = useCallback(
    (ev: AgentEvent) => {
      // Compare against current state outside the updater: the updater must stay
      // pure, or StrictMode's double-invoke fires the reset twice.
      setSelected(selected?.event_id === ev.event_id ? null : ev)
      setPanelTab('data')
      why.reset()
    },
    [selected, why],
  )

  const changePanelTab = useCallback(
    (t: PanelTab) => {
      setPanelTab(t)
      if (t === 'why' && selected) why.load(selected.event_id)
    },
    [selected, why],
  )

  const onSubmitSearch = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault()
      setSelected(null)
      why.reset()
      events.runSearch()
    },
    [events, why],
  )

  // Changing the filter reloads the list, so the open event may no longer be in
  // it — close the panel rather than leave it showing an off-list event.
  const changeFilter = useCallback(
    (type: string | null) => {
      setSelected(null)
      setExpanded(null)
      why.reset()
      return events.applyFilter(type)
    },
    [events, why],
  )

  if (events.loading) return <Skeleton rows={6} />

  if (events.error) {
    return (
      <ErrorState
        message={events.error}
        action={
          <Button size="sm" onClick={events.refresh}>
            Try again
          </Button>
        }
      />
    )
  }

  return (
    <div className="flex flex-col lg:flex-row gap-4">
      <div className="flex-1 min-w-0">
        <form onSubmit={onSubmitSearch} className="flex gap-2 mb-4">
          <div className="relative flex-1">
            <Search
              size={14}
              className="absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none"
              style={{ color: colors.textFaint }}
            />
            <input
              type="search"
              value={events.searchQuery}
              onChange={(e) => events.setSearchQuery(e.target.value)}
              placeholder="Search this agent's events by meaning…"
              aria-label="Search events"
              className="w-full pl-9 pr-9 py-2 text-sm outline-none"
              style={{
                background: colors.surface,
                border: `1px solid ${colors.border}`,
                borderRadius: radii.md,
                color: colors.text,
              }}
            />
            {searchResults && (
              <button
                type="button"
                onClick={events.clearSearch}
                aria-label="Clear search"
                className="absolute right-2 top-1/2 -translate-y-1/2 p-1"
                style={{ color: colors.textMuted }}
              >
                <X size={13} />
              </button>
            )}
          </div>
          <button
            type="submit"
            disabled={events.searching || !events.searchQuery.trim()}
            className="px-4 py-2 text-sm font-medium transition disabled:opacity-40"
            style={{
              background: colors.surfaceHover,
              border: `1px solid ${colors.borderStrong}`,
              borderRadius: radii.md,
              color: colors.text,
            }}
          >
            {events.searching ? 'Searching…' : 'Search'}
          </button>
        </form>

        {events.searchError && (
          <div
            className="mb-4 px-3 py-2.5 text-sm"
            style={{
              background: colors.warningBg,
              border: `1px solid ${colors.warning}40`,
              borderRadius: radii.md,
              color: colors.warning,
            }}
            role="status"
          >
            {events.searchError}
          </div>
        )}

        {/* Type filters — hidden while search results are showing, since they
            filter the chronological list, not the results. */}
        {!searchResults && stats && stats.top_events.length > 0 && (
          <div className="flex gap-1.5 flex-wrap mb-4">
            <FilterPill
              label="All"
              active={!events.filterType}
              onClick={() => changeFilter(null)}
            />
            {stats.top_events.map((e) => (
              <FilterPill
                key={e.event}
                label={`${e.event} (${e.count})`}
                active={events.filterType === e.event}
                color={eventColor(e.event)}
                onClick={() => changeFilter(events.filterType === e.event ? null : e.event)}
              />
            ))}
          </div>
        )}

        {searchResults && (
          <p className="mb-3 text-xs" style={{ color: colors.textMuted }}>
            {searchResults.length} semantic result{searchResults.length !== 1 ? 's' : ''} for
            &quot;{events.searchQuery}&quot;
          </p>
        )}

        {displayEvents.length === 0 ? (
          searchResults ? (
            <EmptyState
              title="No matching events"
              description="Try different wording, or clear the search to browse everything this agent has logged."
              action={
                <Button size="sm" onClick={events.clearSearch}>
                  Clear search
                </Button>
              }
            />
          ) : events.filterType ? (
            <EmptyState
              title={`No ${events.filterType} events`}
              description="This agent hasn't logged any events of this type yet."
              action={
                <Button size="sm" onClick={() => changeFilter(null)}>
                  Show all events
                </Button>
              }
            />
          ) : (
            <EmptyState
              title="No events yet"
              description="Once this agent logs its first event, it will appear here in real time. Send events with its API key through the REST API, an SDK, MCP or a framework integration."
            />
          )
        ) : (
          <>
            <EventList
              events={displayEvents}
              selected={selected}
              expanded={expanded}
              onSelect={selectEvent}
              onToggleExpand={(id) => setExpanded(expanded === id ? null : id)}
            />

            {!searchResults && events.hasMore && (
              <button
                onClick={events.loadMore}
                className="row-hover w-full mt-4 py-2.5 text-sm"
                style={{
                  background: colors.surface,
                  color: colors.text,
                  border: `1px solid ${colors.border}`,
                  borderRadius: radii.md,
                }}
              >
                Load more
              </button>
            )}
          </>
        )}
      </div>

      {selected && (
        <EventDetailPanel
          event={selected}
          tab={panelTab}
          onTabChange={changePanelTab}
          whyChain={why.chain}
          whyLoading={why.loading}
          whyError={why.error}
        />
      )}
    </div>
  )
}
