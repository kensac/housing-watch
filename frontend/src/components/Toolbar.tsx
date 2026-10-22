import { ActionIcon, Button, Checkbox, Group, Popover, Select, Stack, Text, TextInput } from '@mantine/core'
import { useDebouncedCallback } from '@mantine/hooks'
import { IconColumns, IconSearch, IconSortAscending, IconSortDescending } from '@tabler/icons-react'
import { useState } from 'react'

import type { Page, ViewState } from '../api'
import { descending, useMeta, useMotion } from '../hooks'

type Set = (p: Partial<ViewState>, replace?: boolean) => void

/** Quick search over a list; debounced into the URL. */
export function SearchInput({ page, q: current, set }: { page: Page; q: string; set: Set }) {
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

/** Sort and visible columns for a list page. */
export function Toolbar({ page, state, set }: { page: Page; state: ViewState; set: Set }) {
  const pm = useMeta().data!.pages[page]
  const motion = useMotion()
  const sortName = state.sort.replace(/^-/, '')
  const sorts = Object.entries(pm.sorts)
  const desc = descending(state.sort)
  const Direction = desc ? IconSortDescending : IconSortAscending
  return (
    <Group gap="xs" wrap="nowrap">
      {sorts.length > 0 && (
        <Group gap={4} wrap="nowrap">
          <Select size="xs" w={{ base: 170, xs: 190 }} aria-label="Sort by" data={sorts.map(([value, label]) => ({ value, label: `Sort: ${label}` }))} value={sortName}
            onChange={(v) => v && set({ sort: v })} allowDeselect={false} comboboxProps={{ transitionProps: motion }} />
          <ActionIcon size="input-xs" variant="default" aria-label={desc ? 'Sorted descending; sort ascending' : 'Sorted ascending; sort descending'}
            title={desc ? 'Descending' : 'Ascending'}
            onClick={() => set({ sort: state.sort.startsWith('-') ? sortName : `-${sortName}` })}>
            <Direction size={16} aria-hidden />
          </ActionIcon>
        </Group>
      )}
      <Popover position="bottom-end" trapFocus withinPortal transitionProps={motion}>
        <Popover.Target>
          <Button size="xs" variant="default" leftSection={<IconColumns size={15} aria-hidden />}>Columns</Button>
        </Popover.Target>
        <Popover.Dropdown>
          <Checkbox.Group label="Shown columns" size="sm" value={state.columns.filter((c) => c in pm.columns)} onChange={(on) => {
            const next = Object.keys(pm.columns).filter((c) => on.includes(c))
            set({ columns: next.length ? next : pm.default_columns })
          }}>
            <Stack gap={8} mt="xs">
              {Object.entries(pm.columns).map(([k, label]) => <Checkbox key={k} value={k} label={label} />)}
            </Stack>
          </Checkbox.Group>
          <Text size="xs" c="dimmed" mt="sm">Unchecking every column restores the defaults.</Text>
        </Popover.Dropdown>
      </Popover>
    </Group>
  )
}
