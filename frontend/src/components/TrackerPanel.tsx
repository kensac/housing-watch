import { Button, Group, Select, Stack, Text, Timeline } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useState } from 'react'

import { LinkTrackerDoc, request, STAGE_GROUP, UnlinkTrackerDoc, type Listing, type StageStep } from '../api'
import { when } from '../format'
import { useTracker, useTrackerRefresh } from '../hooks'
import { ApplicationBadge } from './Badges'

const HOW: Record<string, string> = {
  url: 'matched by its source link', address: 'matched by address', name: 'matched by building name',
  manual: 'linked by you',
}

/** A tracker row's stages as the syncs saw them change, oldest first. */
function StageTimeline({ steps }: { steps: StageStep[] }) {
  return (
    <Timeline aria-label="Stage history">
      {steps.map((e, i) => (
        <Timeline.Item key={i} title={
          <Group gap="xs">
            {e.stage_to ? <ApplicationBadge s={e.stage_to} group={e.group} /> : <Text size="sm" c="dimmed">No stage</Text>}
            <Text size="xs" c="dimmed">{e.baseline ? `already set when tracking began, ${when(e.at)}` : when(e.at)}</Text>
          </Group>}>
          {i > 0 && <Text size="xs" c="dimmed">from {e.stage_from ?? 'no stage'}</Text>}
        </Timeline.Item>
      ))}
    </Timeline>
  )
}

/** The listing's row in the application tracker sheet (stage, its history and notes), and ways to fix the match. */
export function TrackerPanel({ x }: { x: Listing }) {
  const tracker = useTracker()
  const refresh = useTrackerRefresh()
  const [busy, setBusy] = useState(false)
  const run = async (fn: () => Promise<unknown>, done: string) => {
    setBusy(true)
    try {
      await fn()
      await refresh()
      notifications.show({ message: done })
    } catch (e) {
      notifications.show({ color: 'red', title: 'Couldn’t change the tracker link', message: String((e as Error).message ?? e) })
    } finally {
      setBusy(false)
    }
  }
  const link = (row: string | null) => run(() => request(LinkTrackerDoc, { listing_key: x.key, row_key: row }),
    row ? 'Linked to the tracker row' : 'Marked as not in the tracker')
  const reset = () => run(() => request(UnlinkTrackerDoc, { listing_key: x.key }), 'Back to automatic matching')
  if (tracker.data && !tracker.data.configured) return <Text size="sm" c="dimmed">The tracker sheet isn’t connected.</Text>
  const rows = tracker.data?.rows ?? []
  const row = rows.find((r) => r.property === x.tracker_row)
  return (
    <Stack gap="sm">
      {x.tracker_row ? (
        <>
          <Group gap="xs">
            <Text size="sm" fw={500}>{x.tracker_row}</Text>
            <ApplicationBadge s={x.application} group={x.application_group} since={x.application_since} baseline={x.application_since_baseline} />
            <Text size="xs" c="dimmed">{[x.application_group && STAGE_GROUP[x.application_group], HOW[x.tracker_match ?? '']].filter(Boolean).join(' · ')}</Text>
          </Group>
          {x.application_notes && <Text size="sm" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{x.application_notes}</Text>}
          {row && row.events.length > 0 && <StageTimeline steps={row.events} />}
        </>
      ) : <Text size="sm" c="dimmed">Not in your tracker sheet.</Text>}
      <Group gap="xs" align="flex-end">
        <Select size="xs" w={300} label="Link to a tracker row" placeholder="Pick a row" searchable disabled={busy || !rows.length}
          data={rows.map((r) => ({ value: r.row_key, label: `${r.property}${r.stage ? ` · ${r.stage}` : ''}` }))}
          value={null} onChange={(v) => v && link(v)} nothingFoundMessage="No row matches" />
        {x.tracker_row && x.tracker_match !== 'manual' && (
          <Button size="xs" variant="default" disabled={busy} onClick={() => link(null)}>Not This Row</Button>
        )}
        {x.tracker_match === 'manual' && <Button size="xs" variant="default" disabled={busy} onClick={reset}>Use Automatic Match</Button>}
      </Group>
    </Stack>
  )
}
