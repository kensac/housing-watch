import { ActionIcon, Button, Checkbox, Group, Popover, Select, Stack, Text, TextInput } from '@mantine/core'
import { useDebouncedCallback } from '@mantine/hooks'
import { IconColumns, IconSearch, IconSortAscending, IconSortDescending } from '@tabler/icons-react'
import { useState } from 'react'

import type { Page } from '../api'
import type { ListState } from '../filters'
import { useMeta, useMotion, type SetState } from '../hooks'

/** Quick search over a list; debounced into the URL. */
export function SearchInput({ page, q: current, set }: { page: Page; q: string; set: SetState }) {
  const [q, setQ] = useState(current)
  const [seen, setSeen] = useState(current)
  if (seen !== current) { setSeen(current); setQ(current) } // the URL changed under us (back, a saved view)
  const push = useDebouncedCallback((v: string) => set({ q: v }, true), 300)
  return (
    <TextInput type="search" name="q" aria-label={`Search ${page}`} placeholder="Search title, address, notes…" size="xs" variant="filled"
      w={{ base: '100%', xs: 260 }} leftSection={<IconSearch size={15} aria-hidden />} autoComplete="off" spellCheck={false} value={q}
      onChange={(e) => { setQ(e.currentTarget.value); push(e.currentTarget.value) }} />
  )
}

/** The sort's first field and its direction (later fields only break ties), and the visible columns. */
export function Toolbar({ state, set }: { state: ListState; set: SetState }) {
  const meta = useMeta().data!
  const motion = useMotion()
  const pm = state.page === 'events' ? meta.pages.events : meta.pages[state.page]
  const sorts = state.page === 'events' ? [] : meta.pages[state.page].sorts
  const first = state.page === 'events' ? undefined : state.sort[0]
  const desc = first?.direction === 'desc'
  const Direction = desc ? IconSortDescending : IconSortAscending
  const natural = (field: string) => sorts.find((s) => s.field === field)?.natural ?? 'asc'
  return (
    <Group gap="xs" wrap="nowrap">
      {first && (
        <Group gap={4} wrap="nowrap">
          <Select size="xs" w={{ base: 170, xs: 190 }} aria-label="Sort by" data={sorts.map((s) => ({ value: s.field, label: `Sort: ${s.label}` }))}
            value={first.field} onChange={(v) => v && set({ sort: [{ field: v, direction: natural(v) }] })} allowDeselect={false}
            comboboxProps={{ transitionProps: motion }} />
          <ActionIcon size="input-xs" variant="default" aria-label={desc ? 'Sorted descending; sort ascending' : 'Sorted ascending; sort descending'}
            title={desc ? 'Descending' : 'Ascending'}
            onClick={() => set({ sort: [{ field: first.field, direction: desc ? 'asc' : 'desc' }] })}>
            <Direction size={16} aria-hidden />
          </ActionIcon>
        </Group>
      )}
      <Popover position="bottom-end" trapFocus withinPortal transitionProps={motion}>
        <Popover.Target>
          <Button size="xs" variant="default" leftSection={<IconColumns size={15} aria-hidden />}>Columns</Button>
        </Popover.Target>
        <Popover.Dropdown>
          <Checkbox.Group label="Shown columns" size="sm" value={[...state.columns]} onChange={(on) => {
            const next = pm.columns.map((x) => x.column).filter((c) => on.includes(c))
            set({ columns: next.length ? next : [...pm.default_columns] })
          }}>
            <Stack gap={8} mt="xs">
              {pm.columns.map((x) => <Checkbox key={x.column} value={x.column} label={x.label} />)}
            </Stack>
          </Checkbox.Group>
          <Text size="xs" c="dimmed" mt="sm">Unchecking every column restores the defaults.</Text>
        </Popover.Dropdown>
      </Popover>
    </Group>
  )
}
