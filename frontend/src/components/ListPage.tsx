import { Center, Group, Loader, Stack, Table, Text, UnstyledButton } from '@mantine/core'
import { useIntersection } from '@mantine/hooks'
import { IconChevronDown, IconChevronUp, IconSelector } from '@tabler/icons-react'
import { useEffect, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'

import type { Chip, Page, ViewState } from '../api'
import { stateUrl, useListState, useMeta, useViews } from '../hooks'
import { FilterBar } from './FilterBar'
import { Toolbar } from './Toolbar'
import { ViewBar } from './ViewBar'

export type ListCtx = ReturnType<typeof useListState>

/** A list page: its saved view, toolbar and filters, then whatever table `children` renders for the state. */
export function ListPage({ page, chips, problems, summary, children, search }: {
  page: Page; chips: Chip[]; problems: string[]; summary: ReactNode; search?: boolean
  children: (ctx: ListCtx) => ReactNode
}) {
  const ctx = useListState(page)
  const meta = useMeta().data
  const views = useViews().data
  const navigate = useNavigate()
  // a bare page URL opens the page's default view, if the user made one
  const dflt = views?.find((v) => v.page === page && v.is_default)
  useEffect(() => {
    if (ctx.bare && dflt) navigate(stateUrl(page, dflt.state, meta, dflt.id), { replace: true })
  }, [ctx.bare, dflt, meta, navigate, page])
  if (!ctx.ready) return <Center h={200}><Loader /></Center>
  return (
    <Stack gap="sm">
      <ViewBar page={page} state={ctx.state} view={ctx.view} modified={ctx.modified} />
      <Toolbar page={page} state={ctx.state} set={ctx.set} search={search} />
      <FilterBar page={page} f={ctx.state.f} chips={chips} problems={problems} q={ctx.state.q} onChange={(f) => ctx.set({ f })} />
      <Text size="sm" c="dimmed">{summary}</Text>
      {children(ctx)}
    </Stack>
  )
}

/** A column header that sorts by `sort` (click again to reverse). */
export function SortTh({ label, sort, state, set, ta }: {
  label: string; sort?: string; state: ViewState; set: (p: Partial<ViewState>) => void; ta?: 'right'
}) {
  if (!sort) return <Table.Th ta={ta}>{label}</Table.Th>
  const on = state.sort.replace(/^-/, '') === sort
  const Icon = !on ? IconSelector : state.sort.startsWith('-') ? IconChevronUp : IconChevronDown
  return (
    <Table.Th ta={ta}>
      <UnstyledButton onClick={() => set({ sort: on && !state.sort.startsWith('-') ? `-${sort}` : sort })}>
        <Group gap={4} wrap="nowrap" justify={ta === 'right' ? 'flex-end' : undefined}>
          <Text fw={on ? 700 : 500} size="sm">{label}</Text><Icon size={14} />
        </Group>
      </UnstyledButton>
    </Table.Th>
  )
}

/** The last row of an infinitely scrolling table: loads the next batch when it scrolls into view. */
export function MoreRow({ cols, more, loading, onMore, total, noun }: {
  cols: number; more: boolean; loading: boolean; onMore: () => void; total: number; noun: string
}) {
  const { ref, entry } = useIntersection({ rootMargin: '400px' })
  useEffect(() => { if (entry?.isIntersecting && more && !loading) onMore() }, [entry?.isIntersecting, more, loading, onMore])
  return (
    <Table.Tr ref={ref}>
      <Table.Td colSpan={cols}>
        <Center py="xs">
          {more || loading ? <Loader size="sm" /> : <Text size="sm" c="dimmed">{total ? `All ${total.toLocaleString()} ${noun} shown` : `No ${noun} match`}</Text>}
        </Center>
      </Table.Td>
    </Table.Tr>
  )
}
