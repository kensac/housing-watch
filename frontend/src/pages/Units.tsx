import { Badge, Group, Table, Text } from '@mantine/core'
import { keepPreviousData, useInfiniteQuery } from '@tanstack/react-query'

import { api, qs, type ListResponse, type Unit } from '../api'
import { SizeBadge, StatusBadge, VerdictBadge } from '../components/Badges'
import { ListPage, MoreRow, SortTh } from '../components/ListPage'
import { ListingDrawer } from '../components/ListingDrawer'
import { ago, money } from '../format'
import { useListState } from '../hooks'

const CELLS: Record<string, { label: string; sort?: string; right?: boolean; cell: (u: Unit) => React.ReactNode }> = {
  verdict: { label: 'Verdict', sort: 'verdict', cell: (u) => <VerdictBadge v={u.verdict} why={u.reason} /> },
  size: { label: 'Size', sort: 'size', cell: (u) => <SizeBadge s={u.size} /> },
  unit_no: { label: 'Unit', cell: (u) => u.unit_no ?? '—' },
  rent: { label: 'Rent', sort: 'rent', right: true, cell: (u) => money(u.rent) },
  ami: { label: 'AMI', sort: 'ami', right: true, cell: (u) => (u.ami ? `${u.ami}%` : '—') },
  income: { label: 'Your income band', sort: 'income_min', right: true,
    cell: (u) => (u.income_min == null && u.income_max == null ? '—' : `${money(u.income_min)}–${money(u.income_max)}`) },
  status: { label: 'Status', cell: (u) => <StatusBadge s={u.status} /> },
  borough: { label: 'Borough', sort: 'borough', cell: (u) => u.borough ?? '—' },
  deadline: { label: 'Deadline', sort: 'deadline', cell: (u) => u.deadline ?? '—' },
  source: { label: 'Source', sort: 'source', cell: (u) => u.source },
  changed: { label: 'Changed', sort: 'changed', right: true, cell: (u) => ago(u.last_changed) },
}

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
  const rows = q.data?.pages.flatMap((p) => p.items) ?? []
  const vc = first?.verdict_counts
  return (
    <ListPage page="units" chips={first?.filters ?? []} problems={first?.problems ?? []} summary={first && (
      <>{first.total.toLocaleString()} units{vc && ` · by verdict, ignoring a verdict filter: ${vc.eligible.toLocaleString()} eligible, ${vc.possible.toLocaleString()} possible, ${vc.excluded.toLocaleString()} excluded`}{first.hidden_gone && ' · removed listings hidden'}</>
    )}>
      {({ state, set, open }) => (
        <>
          <Table.ScrollContainer minWidth={900}>
            <Table stickyHeader highlightOnHover verticalSpacing={6}>
              <Table.Thead>
                <Table.Tr>
                  <SortTh label="Building" sort="title" state={state} set={set} />
                  {state.columns.map((c) => <SortTh key={c} label={CELLS[c].label} sort={CELLS[c].sort} state={state} set={set} ta={CELLS[c].right ? 'right' : undefined} />)}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((u) => (
                  <Table.Tr key={u.id} onClick={() => set({ open: u.key })} style={{ cursor: 'pointer' }} opacity={u.verdict === 'excluded' ? 0.6 : 1}>
                    <Table.Td>
                      <Group gap={6} wrap="nowrap">
                        <Text size="sm" fw={500} lineClamp={1}>{u.title}</Text>
                        {u.set_aside && <Badge color="orange" variant="light" size="sm">{u.set_aside}</Badge>}
                      </Group>
                      <Text size="xs" c="dimmed" lineClamp={1}>{[u.address, u.source].filter(Boolean).join(' · ')}</Text>
                    </Table.Td>
                    {state.columns.map((c) => <Table.Td key={c} ta={CELLS[c].right ? 'right' : undefined} style={{ whiteSpace: 'nowrap' }}>{CELLS[c].cell(u)}</Table.Td>)}
                  </Table.Tr>
                ))}
                <MoreRow cols={state.columns.length + 1} more={Boolean(q.hasNextPage)} loading={q.isFetching}
                  onMore={() => q.fetchNextPage()} total={first?.total ?? 0} noun="units" />
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
          <ListingDrawer k={open} onClose={() => set({ open: null })} />
        </>
      )}
    </ListPage>
  )
}
