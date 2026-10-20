import { Anchor, Badge, Group, Select, Stack, Table, Text, Title } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { notifications } from '@mantine/notifications'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { api, qs, type Listing, type ListResponse, type TrackerRow } from '../api'
import { ApplicationBadge, VerdictBadge } from '../components/Badges'
import { ListingDrawer } from '../components/ListingDrawer'
import { Loading, LoadError } from '../components/State'
import { ago, minutes, num, when } from '../format'
import { useTitle, useTracker, useTrackerRefresh } from '../hooks'

/** Search listings by text and link the chosen one to this sheet row. */
function LinkListing({ row }: { row: TrackerRow }) {
  const [search, setSearch] = useState('')
  const [q] = useDebouncedValue(search, 250)
  const refresh = useTrackerRefresh()
  const found = useQuery({
    queryKey: ['link-search', q],
    queryFn: () => api.get<ListResponse<Listing>>(`/api/listings${qs({ q, limit: 20, f: ['gone_since.empty'] })}`),
    enabled: q.trim().length >= 2,
  })
  const link = async (key: string | null) => {
    if (!key) return
    try {
      await api.post('/api/tracker/links', { listing_key: key, row_key: row.row_key })
      await refresh()
      notifications.show({ message: `Linked to “${row.property}”` })
    } catch (e) {
      notifications.show({ color: 'red', title: 'Couldn’t link the listing', message: String((e as Error).message ?? e) })
    }
  }
  return (
    <Select size="xs" w={260} aria-label={`Link a listing to ${row.property}`} placeholder="Link a listing…" searchable
      searchValue={search} onSearchChange={setSearch} value={null} onChange={link} filter={({ options }) => options}
      data={(found.data?.items ?? []).map((x) => ({ value: x.key, label: `${x.title} · ${x.source}` }))}
      nothingFoundMessage={q.trim().length < 2 ? 'Type a building or address' : found.isFetching ? 'Searching…' : 'No listing matches'} />
  )
}

/** Every row of the tracker sheet with the listings it matched; unmatched rows can be linked by hand. */
export function TrackerPage() {
  useTitle('Tracker')
  const q = useTracker()
  const [params, setParams] = useSearchParams()
  const open = params.get('open')
  if (q.error) return <LoadError what="the tracker" error={q.error} retry={() => q.refetch()} />
  if (q.isLoading) return <Loading rows={12} label="Loading the tracker…" />
  const t = q.data!
  const unmatched = t.rows.filter((r) => !r.listings.length).length
  const yours = t.rows.filter((r) => r.group === 'your move').length
  const theirs = t.rows.filter((r) => r.group === 'their move').length
  return (
    <Stack gap="sm">
      <Title order={1} size="h3">Tracker</Title>
      <Text size="sm" c="dimmed">
        {t.configured
          ? <>Your Apartment Search Tracker sheet, read-only, synced every 5 minutes (last <span title={when(t.synced)}>{ago(t.synced)}</span>) · {num(t.rows.length)} rows by stage, longest waiting first · {num(yours)} your move · {num(theirs)} their move · {num(unmatched)} not matched to a listing</>
          : 'The tracker sheet isn’t connected (HW_TRACKER_SHEET).'}
      </Text>
      <Table.ScrollContainer minWidth={1000}>
        <Table highlightOnHover verticalSpacing={6} aria-label="Tracker rows">
          <Table.Thead>
            <Table.Tr><Table.Th ta="right">Row</Table.Th><Table.Th>Property</Table.Th><Table.Th>Stage</Table.Th><Table.Th>Listings</Table.Th><Table.Th>Notes</Table.Th></Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {t.rows.length === 0 && (
              <Table.Tr><Table.Td colSpan={5}><Text size="sm" c="dimmed" ta="center" py="md">The sheet has no rows yet.</Text></Table.Td></Table.Tr>
            )}
            {t.rows.map((r) => (
              <Table.Tr key={r.row_key}>
                <Table.Td ta="right" c="dimmed">{r.position}</Table.Td>
                <Table.Td maw={280}>
                  <Text size="sm" fw={500}>{r.page_url ? <Anchor href={r.page_url} target="_blank" rel="noopener noreferrer" inherit>{r.property}</Anchor> : r.property}</Text>
                  {r.cells.Borough && <Text size="xs" c="dimmed">{r.cells.Borough}</Text>}
                </Table.Td>
                <Table.Td>
                  <ApplicationBadge s={r.stage} group={r.group} since={r.stage_since} baseline={r.stage_since_baseline} />
                  {r.group && <Text size="xs" c="dimmed">{r.group}</Text>}
                  {r.status && r.status !== r.stage && <Text size="xs" c="dimmed">sheet: {r.status}</Text>}
                </Table.Td>
                <Table.Td maw={340}>
                  {r.listings.length ? (
                    <Stack gap={4}>
                      {r.listings.map((x) => (
                        <Group key={x.key} gap={6} wrap="nowrap">
                          <VerdictBadge v={x.verdict} />
                          <Anchor component={Link} to={`?${new URLSearchParams({ open: x.key })}`} size="sm" lineClamp={1}>{x.title}</Anchor>
                          {x.commute != null && <Text size="xs" c="dimmed">{minutes(x.commute)}</Text>}
                          {x.how === 'manual' && <Badge size="xs" variant="outline">linked</Badge>}
                        </Group>
                      ))}
                    </Stack>
                  ) : <LinkListing row={r} />}
                </Table.Td>
                <Table.Td maw={420}><Text size="xs" c="dimmed" lineClamp={3} title={r.notes ?? undefined}>{r.notes ?? '—'}</Text></Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <ListingDrawer k={open} onClose={() => setParams({})} />
    </Stack>
  )
}
