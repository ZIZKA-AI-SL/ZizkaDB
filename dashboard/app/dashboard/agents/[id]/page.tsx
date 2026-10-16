import { redirect } from 'next/navigation'

/**
 * Legacy per-agent route.
 *
 * The dashboard is now organised as top-level tabs with the agent carried in
 * `?agent=`, so this page's content lives in Activity/Behavior. Kept as a
 * permanent redirect so existing links and bookmarks keep working.
 */
export default async function LegacyAgentPage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = await params
  const agent = decodeURIComponent(id)

  redirect(`/dashboard/activity?agent=${encodeURIComponent(agent)}`)
}
