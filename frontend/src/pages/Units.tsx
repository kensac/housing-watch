import { Anchor, Badge, Group, Table, Text } from '@mantine/core'
import { keepPreviousData, useInfiniteQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { Link } from 'react-router-dom'

import { BOROUGH, request, type Unit, type UnitColumn } from '../api'
import { graphql } from '../gql'
import { ApplicationBadge, SizeBadge, StatusBadge, VerdictBadge } from '../components/Badges'
import { LeadTd, ListPage, ListRows, ListTable, MoreRow, SortTh, Summary, type Cells } from '../components/ListPage'
import { ListingDrawer } from '../components/ListingDrawer'
import { hasFilter } from '../filters'
import { ago, date, minutes, money, pct, when } from '../format'
import { useListState } from '../hooks'

const UnitsDoc = graphql(`
  query Units($filter: ListingFilterInput, $q: String! = "", $sort: [UnitSortInput!], $after: String) {
    units(filter: $filter, q: $q, sort: $sort, after: $after) {
      edges { node { ...UnitFields } }
      page_info { has_next_page end_cursor }
      total verdict_counts { eligible possible excluded } filters { ...ChipFields } hidden_gone
    }
  }
`)

const CELLS: Cells<Unit, UnitColumn> = {
  verdict: { label: 'Verdict', sort: 'verdict', cell: (u) => <VerdictBadge v={u.verdict} why={u.reason} /> },
  size: { label: 'Size', sort: 'size', cell: (u) => <SizeBadge s={u.size} /> },
  unit_no: { label: 'Unit', cell: (u) => u.unit_no ?? '—' },
  rent: { label: 'Rent', sort: 'rent', right: true, cell: (u) => money(u.rent) },
  ami: { label: 'AMI', sort: 'ami', right: true, cell: (u) => (u.ami ? pct(u.ami) : '—') },
  income: { label: 'Your Income Band', sort: 'income_min', right: true,
    cell: (u) => (u.income_min == null && u.income_max == null ? '—' : `${money(u.income_min)}–${money(u.income_max)}`) },
  commute: { label: 'Commute', sort: 'commute', right: true, cell: (u) => minutes(u.commute) },
  application: { label: 'Application', sort: 'application', cell: (u) => (
    <ApplicationBadge s={u.application} group={u.application_group} since={u.application_since} baseline={u.application_since_baseline} />) },
  status: { label: 'Status', cell: (u) => <StatusBadge s={u.status} /> },
  borough: { label: 'Borough', sort: 'borough', cell: (u) => (u.borough ? BOROUGH[u.borough] : '—') },
  deadline: { label: 'Deadline', sort: 'deadline', cell: (u) => date(u.deadline) },
  source: { label: 'Source', sort: 'source', cell: (u) => <span translate="no">{u.source}</span> },
  changed: { label: 'Changed', sort: 'changed', right: true, cell: (u) => <span title={when(u.last_changed)}>{ago(u.last_changed)}</span> },
}

const rowKey = (u: Unit) => u.id
const openKey = (u: Unit) => u.key
const dim = (u: Unit) => u.verdict === 'excluded'
const lead = (u: Unit, to: string | null) => (
  <LeadTd>
    <Group gap={6} wrap="nowrap" miw={0}>
      <Anchor component={Link} to={to!} c="inherit" size="sm" fw={500} lineClamp={1}>{u.title}</Anchor>
      {u.set_aside && <Badge color="orange" style={{ minWidth: 'max-content' }}>{u.set_aside}</Badge>}
    </Group>
    <Text size="xs" c="dimmed" lineClamp={1}>{[u.address, u.source].filter(Boolean).join(' · ')}</Text>
  </LeadTd>
)

/** One row per unit, so rent, AMI and income sort exactly; unit filters hold on the row's own unit. */
export function UnitsPage() {
  const ctx = useListState('units')
  const st = ctx.state?.page === 'units' ? ctx.state : null
  const q = useInfiniteQuery({
    queryKey: ['units', st?.filter, st?.q, st?.sort],
    queryFn: ({ pageParam }) =>
      request(UnitsDoc, { filter: st?.filter, q: st?.q, sort: st?.sort, after: pageParam }).then((d) => d.units),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => (last.page_info.has_next_page ? last.page_info.end_cursor : null),
    placeholderData: keepPreviousData,
    enabled: ctx.ready && st !== null,
  })
  const first = q.data?.pages[0]
  const rows = useMemo(() => q.data?.pages.flatMap((p) => p.edges.map((e) => e.node)) ?? [], [q.data])
  return (
    <ListPage page="units" chips={first?.filters ?? []}
      error={q.isError && !q.data ? q.error : undefined} retry={() => q.refetch()}
      summary={first && <Summary total={first.total} noun="units" vc={first.verdict_counts} hiddenGone={first.hidden_gone} />}>
      {({ state, set, base, open }) => {
        const cols = state.page === 'units' ? state.columns : []
        return (
          <>
            <ListTable label="Units" minWidth={900} stale={q.isPlaceholderData}>
              <Table.Thead>
                <Table.Tr>
                  <SortTh label="Building" sort="title" state={state} set={set} lead />
                  {cols.map((c) => <SortTh key={c} label={CELLS[c].label} sort={CELLS[c].sort} state={state} set={set} ta={CELLS[c].right ? 'right' : undefined} />)}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                <ListRows page="units" rows={rows} cols={cols.join(',')} base={base} cells={CELLS} rowKey={rowKey} openKey={openKey} lead={lead} dim={dim} />
                <MoreRow cols={cols.length + 1} more={Boolean(q.hasNextPage)} loading={q.isFetching} failed={q.isFetchNextPageError}
                  onMore={() => q.fetchNextPage()} total={first?.total ?? 0} noun="units"
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
