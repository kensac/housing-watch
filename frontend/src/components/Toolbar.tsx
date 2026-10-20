import { Button, Checkbox, Group, Popover, SegmentedControl, Select, Stack, Text, TextInput } from '@mantine/core'
import { useDebouncedCallback } from '@mantine/hooks'
import { IconColumns, IconSearch } from '@tabler/icons-react'
import { useState } from 'react'

import type { Page, ViewState } from '../api'
import { descending, useMeta, useMotion } from '../hooks'

/** Quick search, sort and visible columns for a list page. */
export function Toolbar({ page, state, set, search = true }: {
  page: Page; state: ViewState; set: (p: Partial<ViewState>, replace?: boolean) => void; search?: boolean
}) {
  const pm = useMeta().data!.pages[page]
  const motion = useMotion()
  const [q, setQ] = useState(state.q)
  const [seen, setSeen] = useState(state.q)
  if (seen !== state.q) { setSeen(state.q); setQ(state.q) } // the URL changed under us (back, a saved view)
  const push = useDebouncedCallback((v: string) => set({ q: v }, true), 300)
  const sortName = state.sort.replace(/^-/, '')
  const sorts = Object.entries(pm.sorts)
  const desc = descending(state.sort)
  return (
    <Group gap="xs" wrap="wrap">
      {search && (
        <TextInput type="search" name="q" aria-label={`Search ${page}`} placeholder="Search title, address, notes…" size="xs" w={{ base: '100%', xs: 260 }}
          leftSection={<IconSearch size={14} aria-hidden />} autoComplete="off" spellCheck={false} value={q}
          onChange={(e) => { setQ(e.currentTarget.value); push(e.currentTarget.value) }} />
      )}
      {sorts.length > 0 && (
        <Group gap={4} wrap="nowrap">
          <Select size="xs" w={{ base: 170, xs: 190 }} aria-label="Sort by" data={sorts.map(([value, label]) => ({ value, label: `Sort: ${label}` }))} value={sortName}
            onChange={(v) => v && set({ sort: v })} allowDeselect={false} comboboxProps={{ transitionProps: motion }} />
          <SegmentedControl size="xs" aria-label="Sort direction" value={desc ? 'desc' : 'asc'} transitionDuration={motion ? 0 : undefined}
            data={[{ value: 'asc', label: 'Asc' }, { value: 'desc', label: 'Desc' }]}
            onChange={(d) => (d === 'desc') !== desc && set({ sort: state.sort.startsWith('-') ? sortName : `-${sortName}` })} />
        </Group>
      )}
      <Popover position="bottom-end" trapFocus withinPortal transitionProps={motion}>
        <Popover.Target>
          <Button size="xs" variant="default" leftSection={<IconColumns size={14} aria-hidden />}>Columns</Button>
        </Popover.Target>
        <Popover.Dropdown>
          <Checkbox.Group label="Shown columns" size="sm" value={state.columns.filter((c) => c in pm.columns)} onChange={(on) => {
            const next = Object.keys(pm.columns).filter((c) => on.includes(c))
            set({ columns: next.length ? next : pm.default_columns })
          }}>
            <Stack gap={6} mt={6}>
              {Object.entries(pm.columns).map(([k, label]) => <Checkbox key={k} value={k} label={label} />)}
            </Stack>
          </Checkbox.Group>
          <Text size="xs" c="dimmed" mt="xs">Unchecking every column restores the defaults.</Text>
        </Popover.Dropdown>
      </Popover>
    </Group>
  )
}
