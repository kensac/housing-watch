import { Table, Text } from '@mantine/core'
import { keepPreviousData, useInfiniteQuery } from '@tanstack/react-query'

import { api, qs, type Event, type EventsResponse } from '../api'
import { EventBadge, VerdictBadge } from '../components/Badges'
import { ListPage, MoreRow } from '../components/ListPage'
import { ListingDrawer } from '../components/ListingDrawer'
import { ago, when } from '../format'
import { useListState } from '../hooks'

const CELLS: Record<string, { label: string; cell: (e: Event) => React.ReactNode }> = {
  verdict: { label: 'Verdict', cell: (e) => <VerdictBadge v={e.verdict} /> },
  source: { label: 'Source', cell: (e) => e.source },
  mailed: { label: 'Mailed', cell: (e) => (e.notified ? ago(e.notified) : '—') },
}

export function EventsPage() {
  const ctx = useListState('events')
  const st = ctx.state
  const q = useInfiniteQuery({
    queryKey: ['events', st.f],
    queryFn: ({ pageParam }) => api.get<EventsResponse>(`/api/events${qs({ f: st.f, before: pageParam })}`),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_before,
    placeholderData: keepPreviousData,
    enabled: ctx.ready,
  })
  const first = q.data?.pages[0]
  const rows = q.data?.pages.flatMap((p) => p.items) ?? []
  return (
    <ListPage page="events" search={false} chips={first?.filters ?? []} problems={first?.problems ?? []}
      summary={first && `${first.total.toLocaleString()} events, newest first`}>
      {({ state, set, open }) => (
        <>
          <Table.ScrollContainer minWidth={800}>
            <Table stickyHeader highlightOnHover verticalSpacing={6}>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Time</Table.Th><Table.Th>Event</Table.Th><Table.Th>What</Table.Th>
                  {state.columns.map((c) => <Table.Th key={c}>{CELLS[c].label}</Table.Th>)}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((e) => (
                  <Table.Tr key={e.id} onClick={() => e.key && set({ open: e.key })} style={{ cursor: e.key ? 'pointer' : undefined }}>
                    <Table.Td style={{ whiteSpace: 'nowrap' }}><Text size="sm" title={when(e.at)}>{ago(e.at)}</Text></Table.Td>
                    <Table.Td><EventBadge t={e.type} baseline={e.baseline} /></Table.Td>
                    <Table.Td><Text size="sm" lineClamp={1}>{e.title ?? e.summary ?? e.source}</Text></Table.Td>
                    {state.columns.map((c) => <Table.Td key={c}>{CELLS[c].cell(e)}</Table.Td>)}
                  </Table.Tr>
                ))}
                <MoreRow cols={state.columns.length + 3} more={Boolean(q.hasNextPage)} loading={q.isFetching}
                  onMore={() => q.fetchNextPage()} total={first?.total ?? 0} noun="events" />
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
          <ListingDrawer k={open} onClose={() => set({ open: null })} />
        </>
      )}
    </ListPage>
  )
}
