import { Button, Group, Select, Stack, Textarea } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useState } from 'react'

import { errorMessage, request, WriteTrackerDoc, type TrackerRow } from '../api'
import { useTracker, useTrackerRefresh } from '../hooks'

/** Write a tracker row's Status and/or Notes, then re-read what depends on them. */
function useWrite(row: TrackerRow) {
  const refresh = useTrackerRefresh()
  const [busy, setBusy] = useState(false)
  const write = async (vars: { status?: string; notes?: string }, done: string) => {
    setBusy(true)
    try {
      await request(WriteTrackerDoc, { row_key: row.row_key, ...vars })
      await refresh()
      notifications.show({ message: done })
    } catch (e) {
      notifications.show({ color: 'red', title: 'Couldn’t change the tracker row', message: errorMessage(e) })
    } finally {
      setBusy(false)
    }
  }
  return { busy, write }
}

/** The row's application stage, as a picker. */
export function StageSelect({ row, label }: { row: TrackerRow; label?: string }) {
  const stages = useTracker().data?.stages ?? []
  const { busy, write } = useWrite(row)
  return (
    <Select size="xs" w={180} label={label} aria-label={label ? undefined : `Stage of ${row.property}`} placeholder="No stage" clearable
      disabled={busy} data={stages} value={row.stage && stages.includes(row.stage) ? row.stage : null}
      onChange={(v) => write({ status: v ?? '' }, v ? `Stage set to ${v}` : 'Stage cleared')} />
  )
}

/** The row's notes, edited in place and saved on demand. */
export function NotesEditor({ row }: { row: TrackerRow }) {
  const [text, setText] = useState(row.notes ?? '')
  const { busy, write } = useWrite(row)
  const changed = text.trim() !== (row.notes ?? '').trim()
  return (
    <Stack gap={4}>
      <Textarea size="xs" label="Notes" autosize minRows={2} maxRows={8} value={text} onChange={(e) => setText(e.currentTarget.value)} />
      {changed && (
        <Group gap="xs">
          <Button size="xs" disabled={busy} onClick={() => write({ notes: text }, 'Notes saved')}>Save Notes</Button>
          <Button size="xs" variant="default" disabled={busy} onClick={() => setText(row.notes ?? '')}>Cancel</Button>
        </Group>
      )}
    </Stack>
  )
}
