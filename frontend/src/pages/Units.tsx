import { Anchor, Badge, Group, Table, Text } from '@mantine/core'
import { keepPreviousData, useInfiniteQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { Link } from 'react-router-dom'

import { api, qs, type ListResponse, type Unit } from '../api'
import { ApplicationBadge, SizeBadge, StatusBadge, VerdictBadge } from '../components/Badges'
import { ListPage, ListRows, ListTable, MoreRow, SortTh, Summary, type Cells } from '../components/ListPage'
import { ListingDrawer } from '../components/ListingDrawer'
import { ago, date, minutes, money, pct, when } from '../format'
import { useListState } from '../hooks'

const CELLS: Cells<Unit> = {
  verdict: { label: 'Verdict', sort: 'verdict', cell: (u) => <VerdictBadge v={u.verdict} why={u.reason} /> },
  size: { label: 'Size', sort: 'size', cell: (u) => <SizeBadge s={u.size} /> },
  unit_no: { label: 'Unit', cell: (u) => u.unit_no ?? '—' },
  rent: { label: 'Rent', sort: 'rent', right: true, cell: (u) => money(u.rent) },
  ami: { label: 'AMI', sort: 'ami', right: true, cell: (u) => (u.ami ? pct(u.ami) : '—') },
  income: { label: 'Your Income Band', sort: 'income_min', right: true,
    cell: (u) => (u.income_min == null && u.income_max == null ? '—' : `${money(u.income_min)}–${money(u.income_max)}`) },
  commute: { label: 'Commute', sort: 'commute', right: true, cell: (u) => minutes(u.commute) },
  application: { label: 'Application', cell: (u) => <ApplicationBadge s={u.application} /> },
  status: { label: 'Status', cell: (u) => <StatusBadge s={u.status} /> },
  borough: { label: 'Borough', sort: 'borough', cell: (u) => u.borough ?? '—' },
  deadline: { label: 'Deadline', sort: 'deadline', cell: (u) => date(u.deadline) },
  source: { label: 'Source', sort: 'source', cell: (u) => <span translate="no">{u.source}</span> },
  changed: { label: 'Changed', sort: 'changed', right: true, cell: (u) => <span title={when(u.last_changed)}>{ago(u.last_changed)}</span> },
}

const rowKey = (u: Unit) => u.id
const openKey = (u: Unit) => u.key
const dim = (u: Unit) => u.verdict === 'excluded'
const lead = (u: Unit, to: string | null) => (
  <Table.Td maw={420}>
    <Group gap={6} wrap="nowrap" miw={0}>
      <Anchor component={Link} to={to!} c="inherit" size="sm" fw={500} lineClamp={1}>{u.title}</Anchor>
      {u.set_aside && <Badge color="orange" variant="light" size="sm" style={{ minWidth: 'max-content' }}>{u.set_aside}</Badge>}
    </Group>
    <Text size="xs" c="dimmed" lineClamp={1}>{[u.address, u.source].filter(Boolean).join(' · ')}</Text>
  </Table.Td>
)

/** One row per unit, so rent, AMI and income sort exactly; unit filters hold on the row's own unit. */
export function UnitsPage() {
  const ctx = useListState('units')
  const st = ctx.state
  const q = useInfiniteQuery({
    queryKey: ['units', st.f, st.q, st.sort],
    queryFn: ({ pageParam }) => api.get<ListResponse<Unit>>(`/api/units${qs({ f: st.f, q: st.q, sort: st.sort, offset: pageParam })}`),
    initialPageParam: 0,
    getNextPageParam: (last) => last.next_offset,
    placeholderData: keepPreviousData,
    enabled: ctx.ready,
  })
  const first = q.data?.pages[0]
  const rows = useMemo(() => q.data?.pages.flatMap((p) => p.items) ?? [], [q.data])
  return (
    <ListPage page="units" chips={first?.filters ?? []} problems={first?.problems ?? []}
      error={q.isError && !q.data ? q.error : undefined} retry={() => q.refetch()}
      summary={first && <Summary total={first.total} noun="units" vc={first.verdict_counts} hiddenGone={first.hidden_gone} />}>
      {({ state, set, base, open }) => {
        const cols = state.columns.filter((c) => c in CELLS) // a column the server added before this app knows it
        return (
          <>
            <ListTable label="Units" minWidth={900} stale={q.isPlaceholderData}>
              <Table.Thead>
                <Table.Tr>
                  <SortTh label="Building" sort="title" state={state} set={set} />
                  {cols.map((c) => <SortTh key={c} label={CELLS[c].label} sort={CELLS[c].sort} state={state} set={set} ta={CELLS[c].right ? 'right' : undefined} />)}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                <ListRows page="units" rows={rows} cols={cols.join(',')} base={base} cells={CELLS} rowKey={rowKey} openKey={openKey} lead={lead} dim={dim} />
                <MoreRow cols={cols.length + 1} more={Boolean(q.hasNextPage)} loading={q.isFetching} failed={q.isFetchNextPageError}
                  onMore={() => q.fetchNextPage()} total={first?.total ?? 0} noun="units"
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
