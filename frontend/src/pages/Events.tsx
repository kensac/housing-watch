import { Anchor, Table, Text } from '@mantine/core'
import { keepPreviousData, useInfiniteQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { Link } from 'react-router-dom'

import { api, qs, type Event, type EventsResponse } from '../api'
import { EventBadge, VerdictBadge } from '../components/Badges'
import { LeadTd, ListPage, ListRows, ListTable, MoreRow, SortTh, type Cells } from '../components/ListPage'
import { ListingDrawer } from '../components/ListingDrawer'
import { ago, num, when } from '../format'
import { useListState } from '../hooks'

const CELLS: Cells<Event> = {
  verdict: { label: 'Verdict', cell: (e) => <VerdictBadge v={e.verdict} /> },
  source: { label: 'Source', cell: (e) => <span translate="no">{e.source}</span> },
  mailed: { label: 'Mailed', cell: (e) => (e.notified ? <span title={when(e.notified)}>{ago(e.notified)}</span> : '—') },
}

const rowKey = (e: Event) => e.id
const openKey = (e: Event) => e.key
const lead = (e: Event, to: string | null) => {
  const what = e.title ?? e.summary ?? e.source
  return (
    <>
      <Table.Td style={{ whiteSpace: 'nowrap' }} c="dimmed" title={when(e.at)}>{ago(e.at)}</Table.Td>
      <Table.Td><EventBadge t={e.type} baseline={e.baseline} /></Table.Td>
      <LeadTd>
        {to
          ? <Anchor component={Link} to={to} c="inherit" size="sm" fw={500} lineClamp={1}>{what}</Anchor>
          : <Text size="sm" lineClamp={1}>{what}</Text>}
      </LeadTd>
    </>
  )
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
  const rows = useMemo(() => q.data?.pages.flatMap((p) => p.items) ?? [], [q.data])
  return (
    <ListPage page="events" search={false} chips={first?.filters ?? []} problems={first?.problems ?? []}
      error={q.isError && !q.data ? q.error : undefined} retry={() => q.refetch()}
      summary={first && `${num(first.total)} events, newest first`}>
      {({ state, set, base, open }) => {
        const cols = state.columns.filter((c) => c in CELLS)
        return (
          <>
            <ListTable label="Events" minWidth={800} stale={q.isPlaceholderData}>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Time</Table.Th><Table.Th>Event</Table.Th><SortTh label="What" lead />
                  {cols.map((c) => <Table.Th key={c}>{CELLS[c].label}</Table.Th>)}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                <ListRows page="events" rows={rows} cols={cols.join(',')} base={base} cells={CELLS} rowKey={rowKey} openKey={openKey} lead={lead} />
                <MoreRow cols={cols.length + 3} more={Boolean(q.hasNextPage)} loading={q.isFetching} failed={q.isFetchNextPageError}
                  onMore={() => q.fetchNextPage()} total={first?.total ?? 0} noun="events"
                  onClear={state.f.length ? () => set({ f: [] }) : undefined} clearLabel="Clear Filters" />
              </Table.Tbody>
            </ListTable>
            <ListingDrawer k={open} onClose={() => set({ open: null })} />
          </>
        )
      }}
    </ListPage>
  )
}
