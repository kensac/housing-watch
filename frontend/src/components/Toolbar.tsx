import { Button, Checkbox, Group, Menu, Select, Stack, Text, TextInput } from '@mantine/core'
import { useDebouncedCallback } from '@mantine/hooks'
import { IconColumns, IconSearch } from '@tabler/icons-react'
import { useState } from 'react'

import type { Page, ViewState } from '../api'
import { useMeta } from '../hooks'

/** Quick search, sort and visible columns for a list page. */
export function Toolbar({ page, state, set, search = true }: {
  page: Page; state: ViewState; set: (p: Partial<ViewState>, replace?: boolean) => void; search?: boolean
}) {
  const pm = useMeta().data!.pages[page]
  const [q, setQ] = useState(state.q)
  const [seen, setSeen] = useState(state.q)
  if (seen !== state.q) { setSeen(state.q); setQ(state.q) } // the URL changed under us (back, a saved view)
  const push = useDebouncedCallback((v: string) => set({ q: v }, true), 300)
  const sortName = state.sort.replace(/^-/, '')
  const sorts = Object.entries(pm.sorts)
  return (
    <Group gap="xs" wrap="wrap">
      {search && (
        <TextInput placeholder="Search title, address, notes" leftSection={<IconSearch size={14} />} value={q} size="xs" w={260}
          onChange={(e) => { setQ(e.currentTarget.value); push(e.currentTarget.value) }} />
      )}
      {sorts.length > 0 && (
        <Group gap={4} wrap="nowrap">
          <Select size="xs" w={190} data={sorts.map(([value, label]) => ({ value, label: `Sort: ${label}` }))} value={sortName}
            onChange={(v) => v && set({ sort: v })} allowDeselect={false} />
          <Button size="xs" variant="default" onClick={() => set({ sort: state.sort.startsWith('-') ? sortName : `-${sortName}` })}>
            {state.sort.startsWith('-') ? 'Reversed' : 'Default order'}
          </Button>
        </Group>
      )}
      <Menu closeOnItemClick={false} position="bottom-end">
        <Menu.Target><Button size="xs" variant="default" leftSection={<IconColumns size={14} />}>Columns</Button></Menu.Target>
        <Menu.Dropdown>
          <Stack gap={6} p="xs">
            <Text size="xs" c="dimmed">Shown columns</Text>
            {Object.entries(pm.columns).map(([k, label]) => (
              <Checkbox key={k} label={label} checked={state.columns.includes(k)} onChange={(e) => {
                const on = e.currentTarget.checked
                const next = Object.keys(pm.columns).filter((c) => (c === k ? on : state.columns.includes(c)))
                set({ columns: next.length ? next : pm.default_columns })
              }} />
            ))}
          </Stack>
        </Menu.Dropdown>
      </Menu>
    </Group>
  )
}
