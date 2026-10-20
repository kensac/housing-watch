import { Anchor, Group, Table, Text } from '@mantine/core'
import { keepPreviousData, useInfiniteQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { Link } from 'react-router-dom'

import { api, qs, type Listing, type ListResponse, type ViewState } from '../api'
import { SizeBadge, StatusBadge, VerdictBadge } from '../components/Badges'
import { ListPage, ListRows, ListTable, MoreRow, SortTh, Summary, type Cells } from '../components/ListPage'
import { ListingDrawer } from '../components/ListingDrawer'
import { ago, date, minutes, money, pct, range, when } from '../format'
import { useListState } from '../hooks'

const CELLS: Cells<Listing> = {
  verdict: { label: 'Verdict', sort: 'verdict', cell: (x) => <VerdictBadge v={x.verdict} why={x.reason} /> },
  sizes: { label: 'Sizes', cell: (x) => (x.sizes.length ? <Group gap={4} wrap="nowrap">{x.sizes.map((s) => <SizeBadge key={s} s={s} />)}</Group> : '—') },
  rent: { label: 'Rent (Fits You)', sort: 'rent', right: true, cell: (x) => range(x.rent_min, x.rent_max, money) },
  ami: { label: 'AMI', right: true, cell: (x) => range(x.ami_min, x.ami_max, pct) },
  commute: { label: 'Commute', sort: 'commute', right: true, cell: (x) => minutes(x.commute) },
  status: { label: 'Status', cell: (x) => <StatusBadge s={x.status} /> },
  deadline: { label: 'Deadline', sort: 'deadline', cell: (x) => date(x.deadline) },
  borough: { label: 'Borough', sort: 'borough', cell: (x) => x.borough ?? '—' },
  kind: { label: 'Kind', cell: (x) => x.kind },
  source: { label: 'Source', sort: 'source', cell: (x) => <span translate="no">{x.source}</span> },
  first_seen: { label: 'First Seen', sort: 'first_seen', right: true, cell: (x) => <span title={when(x.first_seen)}>{ago(x.first_seen)}</span> },
  changed: { label: 'Changed', sort: 'changed', right: true, cell: (x) => <span title={when(x.last_changed)}>{ago(x.last_changed)}</span> },
}

const rowKey = (x: Listing) => x.key
const dim = (x: Listing) => x.status === 'gone'
const lead = (x: Listing, to: string | null) => (
  <Table.Td maw={420}>
    <Anchor component={Link} to={to!} c="inherit" size="sm" fw={500} lineClamp={1}>{x.title}</Anchor>
    <Text size="xs" c="dimmed" lineClamp={1}>{[x.address, x.borough].filter(Boolean).join(' · ') || x.source}</Text>
  </Table.Td>
)

function useListings(st: ViewState, enabled: boolean) {
  return useInfiniteQuery({
    queryKey: ['listings', st.f, st.q, st.sort],
    queryFn: ({ pageParam }) => api.get<ListResponse<Listing>>(`/api/listings${qs({ f: st.f, q: st.q, sort: st.sort, offset: pageParam })}`),
    initialPageParam: 0,
    getNextPageParam: (last) => last.next_offset,
    placeholderData: keepPreviousData,
    enabled,
  })
}

export function ListingsPage() {
  const ctx = useListState('listings')
  const q = useListings(ctx.state, ctx.ready)
  const first = q.data?.pages[0]
  const rows = useMemo(() => q.data?.pages.flatMap((p) => p.items) ?? [], [q.data])
  return (
    <ListPage page="listings" chips={first?.filters ?? []} problems={first?.problems ?? []}
      error={q.isError && !q.data ? q.error : undefined} retry={() => q.refetch()}
      summary={first && <Summary total={first.total} noun="listings" vc={first.verdict_counts} hiddenGone={first.hidden_gone} />}>
      {({ state, set, base, open }) => {
        const cols = state.columns.filter((c) => c in CELLS) // a column the server added before this app knows it
        return (
          <>
            <ListTable label="Listings" minWidth={900} stale={q.isPlaceholderData}>
              <Table.Thead>
                <Table.Tr>
                  <SortTh label="Listing" sort="title" state={state} set={set} />
                  {cols.map((c) => <SortTh key={c} label={CELLS[c].label} sort={CELLS[c].sort} state={state} set={set} ta={CELLS[c].right ? 'right' : undefined} />)}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                <ListRows page="listings" rows={rows} cols={cols.join(',')} base={base} cells={CELLS} rowKey={rowKey} openKey={rowKey} lead={lead} dim={dim} />
                <MoreRow cols={cols.length + 1} more={Boolean(q.hasNextPage)} loading={q.isFetching} failed={q.isFetchNextPageError}
                  onMore={() => q.fetchNextPage()} total={first?.total ?? 0} noun="listings"
                  onClear={state.f.length || state.q ? () => set({ f: [], q: '' }) : undefined} />
              </Table.Tbody>
            </ListTable>
            <ListingDrawer k={open} onClose={() => set({ open: null })} />
          </>
        )
      }}
    </ListPage>
  )
}
