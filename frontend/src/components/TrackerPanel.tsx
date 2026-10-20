import { Button, Group, Select, Stack, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useState } from 'react'

import { api, qs, type Listing } from '../api'
import { useTracker, useTrackerRefresh } from '../hooks'
import { ApplicationBadge } from './Badges'

const HOW: Record<string, string> = {
  url: 'matched by its source link', address: 'matched by address', name: 'matched by building name',
  manual: 'linked by you',
}

/** The listing's row in the application tracker sheet (status and notes), and ways to fix the match. */
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
  const link = (row: string | null) => run(() => api.post('/api/tracker/links', { listing_key: x.key, row_key: row }),
    row ? 'Linked to the tracker row' : 'Marked as not in the tracker')
  const reset = () => run(() => api.delete(`/api/tracker/links${qs({ listing_key: x.key })}`), 'Back to automatic matching')
  if (tracker.data && !tracker.data.configured) return <Text size="sm" c="dimmed">The tracker sheet isn’t connected.</Text>
  const rows = tracker.data?.rows ?? []
  return (
    <Stack gap="xs">
      {x.tracker_row ? (
        <>
          <Group gap="xs">
            <ApplicationBadge s={x.application} />
            <Text size="sm" fw={500}>{x.tracker_row}</Text>
            <Text size="xs" c="dimmed">{HOW[x.tracker_match ?? ''] ?? ''}</Text>
          </Group>
          {x.application_notes && <Text size="sm" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{x.application_notes}</Text>}
        </>
      ) : <Text size="sm" c="dimmed">Not in your tracker sheet.</Text>}
      <Group gap="xs" align="flex-end">
        <Select size="xs" w={300} label="Link to a tracker row" placeholder="Pick a row" searchable disabled={busy || !rows.length}
          data={rows.map((r) => ({ value: r.row_key, label: `${r.property}${r.status ? ` · ${r.status}` : ''}` }))}
          value={null} onChange={(v) => v && link(v)} nothingFoundMessage="No row matches" />
        {x.tracker_row && x.tracker_match !== 'manual' && (
          <Button size="xs" variant="default" disabled={busy} onClick={() => link(null)}>Not This Row</Button>
        )}
        {x.tracker_match === 'manual' && <Button size="xs" variant="default" disabled={busy} onClick={reset}>Use Automatic Match</Button>}
      </Group>
    </Stack>
  )
}
