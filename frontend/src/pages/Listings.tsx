import { Group, Table, Text } from '@mantine/core'
import { keepPreviousData, useInfiniteQuery } from '@tanstack/react-query'

import { api, qs, type Listing, type ListResponse, type ViewState } from '../api'
import { SizeBadge, StatusBadge, VerdictBadge } from '../components/Badges'
import { ListPage, MoreRow, SortTh } from '../components/ListPage'
import { ListingDrawer } from '../components/ListingDrawer'
import { ago, money, range } from '../format'
import { useListState } from '../hooks'

const CELLS: Record<string, { label: string; sort?: string; right?: boolean; cell: (x: Listing) => React.ReactNode }> = {
  verdict: { label: 'Verdict', sort: 'verdict', cell: (x) => <VerdictBadge v={x.verdict} why={x.reason} /> },
  sizes: { label: 'Sizes', cell: (x) => <Group gap={4}>{x.sizes.map((s) => <SizeBadge key={s} s={s} />)}</Group> },
  rent: { label: 'Rent (fits you)', sort: 'rent', right: true, cell: (x) => range(x.rent_min, x.rent_max, (v) => money(v)) },
  ami: { label: 'AMI', right: true, cell: (x) => range(x.ami_min, x.ami_max, (v) => `${v}%`) },
  status: { label: 'Status', cell: (x) => <StatusBadge s={x.status} /> },
  deadline: { label: 'Deadline', sort: 'deadline', cell: (x) => x.deadline ?? '—' },
  borough: { label: 'Borough', sort: 'borough', cell: (x) => x.borough ?? '—' },
  kind: { label: 'Kind', cell: (x) => x.kind },
  source: { label: 'Source', sort: 'source', cell: (x) => x.source },
  first_seen: { label: 'First seen', sort: 'first_seen', right: true, cell: (x) => ago(x.first_seen) },
  changed: { label: 'Changed', sort: 'changed', right: true, cell: (x) => ago(x.last_changed) },
}

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
  const rows = q.data?.pages.flatMap((p) => p.items) ?? []
  const vc = first?.verdict_counts
  return (
    <ListPage page="listings" chips={first?.filters ?? []} problems={first?.problems ?? []} summary={first && (
      <>{first.total.toLocaleString()} listings{vc && ` · by verdict, ignoring a verdict filter: ${vc.eligible.toLocaleString()} eligible, ${vc.possible.toLocaleString()} possible, ${vc.excluded.toLocaleString()} excluded`}{first.hidden_gone && ' · removed listings hidden'}</>
    )}>
      {({ state, set, open }) => (
        <>
          <Table.ScrollContainer minWidth={900}>
            <Table stickyHeader highlightOnHover verticalSpacing={6}>
              <Table.Thead>
                <Table.Tr>
                  <SortTh label="Listing" sort="title" state={state} set={set} />
                  {state.columns.map((c) => <SortTh key={c} label={CELLS[c].label} sort={CELLS[c].sort} state={state} set={set} ta={CELLS[c].right ? 'right' : undefined} />)}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((x) => (
                  <Table.Tr key={x.key} onClick={() => set({ open: x.key })} style={{ cursor: 'pointer' }} opacity={x.status === 'gone' ? 0.6 : 1}>
                    <Table.Td>
                      <Text size="sm" fw={500} lineClamp={1}>{x.title}</Text>
                      <Text size="xs" c="dimmed" lineClamp={1}>{[x.address, x.borough].filter(Boolean).join(' · ') || x.source}</Text>
                    </Table.Td>
                    {state.columns.map((c) => <Table.Td key={c} ta={CELLS[c].right ? 'right' : undefined} style={{ whiteSpace: 'nowrap' }}>{CELLS[c].cell(x)}</Table.Td>)}
                  </Table.Tr>
                ))}
                <MoreRow cols={state.columns.length + 1} more={Boolean(q.hasNextPage)} loading={q.isFetching}
                  onMore={() => q.fetchNextPage()} total={first?.total ?? 0} noun="listings" />
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
          <ListingDrawer k={open} onClose={() => set({ open: null })} />
        </>
      )}
    </ListPage>
  )
}
