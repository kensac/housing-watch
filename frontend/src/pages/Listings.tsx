import { Anchor, Group, Table, Text } from '@mantine/core'
import { keepPreviousData, useInfiniteQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { Link } from 'react-router-dom'

import { BOROUGH, KIND, ListingsDoc, request, type Listing, type ListingColumn } from '../api'
import { ApplicationBadge, SizeBadge, StatusBadge, VerdictBadge } from '../components/Badges'
import { LeadTd, ListPage, ListRows, ListTable, MoreRow, SortTh, Summary, type Cells } from '../components/ListPage'
import { hasFilter, type ListingsState } from '../filters'
import { ListingDrawer } from '../components/ListingDrawer'
import { ago, date, minutes, money, pct, range, when } from '../format'
import { useListState } from '../hooks'

const CELLS: Cells<Listing, ListingColumn> = {
  verdict: { label: 'Verdict', sort: 'verdict', cell: (x) => <VerdictBadge v={x.verdict} why={x.reason} /> },
  // a fixed width, so a listing with many sizes wraps onto the row's second line instead of widening the column
  sizes: { label: 'Sizes', cell: (x) => (x.sizes.length ? <Group gap={4} w={144}>{x.sizes.map((s) => <SizeBadge key={s} s={s} />)}</Group> : '—') },
  rent: { label: 'Rent (Fits You)', sort: 'rent', right: true, cell: (x) => range(x.rent_min, x.rent_max, money) },
  ami: { label: 'AMI', right: true, cell: (x) => range(x.ami_min, x.ami_max, pct) },
  commute: { label: 'Commute', sort: 'commute', right: true, cell: (x) => minutes(x.commute) },
  application: { label: 'Application', sort: 'application', cell: (x) => (
    <ApplicationBadge s={x.application} group={x.application_group} since={x.application_since} baseline={x.application_since_baseline} />) },
  status: { label: 'Status', cell: (x) => <StatusBadge s={x.status} /> },
  deadline: { label: 'Deadline', sort: 'deadline', cell: (x) => date(x.deadline) },
  borough: { label: 'Borough', sort: 'borough', cell: (x) => (x.borough ? BOROUGH[x.borough] : '—') },
  kind: { label: 'Kind', cell: (x) => KIND[x.kind] },
  source: { label: 'Source', sort: 'source', cell: (x) => <span translate="no">{x.source}</span> },
  first_seen: { label: 'First Seen', sort: 'first_seen', right: true, cell: (x) => <span title={when(x.first_seen)}>{ago(x.first_seen)}</span> },
  changed: { label: 'Changed', sort: 'changed', right: true, cell: (x) => <span title={when(x.last_changed)}>{ago(x.last_changed)}</span> },
}

const rowKey = (x: Listing) => x.key
const dim = (x: Listing) => x.status === 'gone'
const lead = (x: Listing, to: string | null) => (
  <LeadTd>
    <Anchor component={Link} to={to!} c="inherit" size="sm" fw={500} lineClamp={1}>{x.title}</Anchor>
    <Text size="xs" c="dimmed" lineClamp={1}>{[x.address, x.borough && BOROUGH[x.borough]].filter(Boolean).join(' · ') || x.source}</Text>
  </LeadTd>
)

function useListings(st: ListingsState | null, enabled: boolean) {
  return useInfiniteQuery({
    queryKey: ['listings', st?.filter, st?.q, st?.sort],
    queryFn: ({ pageParam }) =>
      request(ListingsDoc, { filter: st?.filter, q: st?.q, sort: st?.sort, after: pageParam }).then((d) => d.listings),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => (last.page_info.has_next_page ? last.page_info.end_cursor : null),
    placeholderData: keepPreviousData,
    enabled: enabled && st !== null,
  })
}

export function ListingsPage() {
  const ctx = useListState('listings')
  const q = useListings(ctx.state?.page === 'listings' ? ctx.state : null, ctx.ready)
  const first = q.data?.pages[0]
  const rows = useMemo(() => q.data?.pages.flatMap((p) => p.edges.map((e) => e.node)) ?? [], [q.data])
  return (
    <ListPage page="listings" chips={first?.filters ?? []}
      error={q.isError && !q.data ? q.error : undefined} retry={() => q.refetch()}
      summary={first && <Summary total={first.total} noun="listings" vc={first.verdict_counts} hiddenGone={first.hidden_gone} />}>
      {({ state, set, base, open }) => {
        const cols = state.page === 'listings' ? state.columns : []
        return (
          <>
            <ListTable label="Listings" minWidth={900} stale={q.isPlaceholderData}>
              <Table.Thead>
                <Table.Tr>
                  <SortTh label="Listing" sort="title" state={state} set={set} lead />
                  {cols.map((c) => <SortTh key={c} label={CELLS[c].label} sort={CELLS[c].sort} state={state} set={set} ta={CELLS[c].right ? 'right' : undefined} />)}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                <ListRows page="listings" rows={rows} cols={cols.join(',')} base={base} cells={CELLS} rowKey={rowKey} openKey={rowKey} lead={lead} dim={dim} />
                <MoreRow cols={cols.length + 1} more={Boolean(q.hasNextPage)} loading={q.isFetching} failed={q.isFetchNextPageError}
                  onMore={() => q.fetchNextPage()} total={first?.total ?? 0} noun="listings"
                  onClear={hasFilter(state) || (state.page !== 'events' && state.q) ? () => set({ filter: {}, q: '' }) : undefined} />
              </Table.Tbody>
            </ListTable>
            <ListingDrawer k={open} onClose={() => set({ open: null })} />
          </>
        )
      }}
    </ListPage>
  )
}
