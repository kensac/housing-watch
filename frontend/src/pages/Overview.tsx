import { Alert, Anchor, Card, Code, Group, SimpleGrid, Stack, Table, Text, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'

import { api, type Overview } from '../api'
import { EventBadge, StatusBadge, VerdictBadge } from '../components/Badges'
import { ListingDrawer } from '../components/ListingDrawer'
import { Loading, LoadError } from '../components/State'
import { ago, money, num, range, when } from '../format'
import { rowClick, useMeta, useTitle } from '../hooks'

function Stat({ label, value, hint, to }: { label: string; value: number; hint?: string; to?: string }) {
  return (
    <Card withBorder padding="md">
      <Text size="sm" c="dimmed">{label}</Text>
      <Text fz="h2" fw={700} lh="h2">{to ? <Anchor component={Link} to={to} inherit>{num(value)}</Anchor> : num(value)}</Text>
      {hint && <Text size="xs" c="dimmed">{hint}</Text>}
    </Card>
  )
}

const openSearch = (key: string) => `?${new URLSearchParams({ open: key })}`

export function OverviewPage() {
  useTitle('Overview')
  const q = useQuery({ queryKey: ['overview'], queryFn: () => api.get<Overview>('/api/overview'), refetchInterval: 60_000 })
  const meta = useMeta()
  const [params, setParams] = useSearchParams()
  const open = params.get('open')
  if (q.error || meta.error) return <LoadError what="the overview" error={q.error ?? meta.error} retry={() => { q.refetch(); meta.refetch() }} />
  if (q.isLoading || !meta.data) return <Loading rows={12} label="Loading the overview…" />
  const o = q.data!
  const m = meta.data
  const p = m.profile
  const statuses = m.statuses.filter((s) => s !== 'gone')
  return (
    <Stack gap="lg">
      <Stack gap={2}>
        <Title order={1} size="h3">Overview</Title>
        <Text size="sm" c="dimmed">
          Your profile: {p.household} person · {money(p.income)} · {p.sizes.join(', ')}{p.excluded_boroughs.length ? ` · not ${p.excluded_boroughs.join(', ')}` : ''}
          {' · '}{num(o.total)} listings from {num(o.n_sources)} sources
        </Text>
      </Stack>
      {o.failing.length > 0 && (
        <Alert color="red" variant="light" title={`${o.failing.length} source${o.failing.length > 1 ? 's' : ''} failing`}>
          <span translate="no">{o.failing.join(', ')}</span>. Listings from {o.failing.length > 1 ? 'these sources' : 'this source'} may be out of date.{' '}
          <Anchor component={Link} to="/sources" size="sm">See errors on Sources</Anchor>
        </Alert>
      )}
      <SimpleGrid cols={{ base: 2, sm: 3, lg: 6 }}>
        <Stat label="Eligible & open" value={o.eligible_open} hint={`${num(o.eligible_live)} incl. waitlist / upcoming`}
          to="/listings?f=verdict.in.eligible&f=status.in.open" />
        <Stat label="New in 24h" value={o.new_24h} hint={`${num(o.new_7d)} in 7 days${o.baseline_24h ? ` · ${num(o.baseline_24h)} first-run` : ''}`} />
        <Stat label="Changed in 24h" value={o.changed_24h} hint={`${num(o.changed_7d)} in 7 days`} />
        <Stat label="Digest pending" value={o.pending} hint={`last sent ${ago(o.last_digest)}`} />
        <Stat label="Sources" value={o.n_sources} hint={o.failing.length ? `${o.failing.length} failing` : 'all healthy'} to="/sources" />
        <Stat label="Listings" value={o.total} to="/listings" />
      </SimpleGrid>

      <SimpleGrid cols={{ base: 1, lg: 2 }}>
        <Card withBorder>
          <Title order={2} size="h5" mb="sm">Eligible Now</Title>
          {o.eligible_now.length ? (
            <Table.ScrollContainer minWidth={420}>
              <Table highlightOnHover tabularNums aria-label="Eligible listings">
                <Table.Tbody>
                  {o.eligible_now.map((x) => (
                    <Table.Tr key={x.key} onClick={rowClick(() => setParams({ open: x.key }))} style={{ cursor: 'pointer' }}>
                      <Table.Td>
                        <Anchor component={Link} to={openSearch(x.key)} c="inherit" size="sm" fw={500} lineClamp={1}>{x.title}</Anchor>
                        <Text size="xs" c="dimmed">{x.borough ?? '—'} · <span translate="no">{x.source}</span></Text>
                      </Table.Td>
                      <Table.Td ta="right" style={{ whiteSpace: 'nowrap' }}>{range(x.rent_min, x.rent_max, money)}</Table.Td>
                      <Table.Td><StatusBadge s={x.status} /></Table.Td>
                      <Table.Td ta="right" style={{ whiteSpace: 'nowrap' }}><Text size="xs" c="dimmed" title={when(x.last_changed)}>{ago(x.last_changed)}</Text></Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          ) : (
            <Text size="sm" c="dimmed">
              Nothing you qualify for is open right now. New matches show up here and in the next digest;{' '}
              <Anchor component={Link} to="/listings?f=verdict.in.eligible" size="sm">see every eligible listing</Anchor>.
            </Text>
          )}
        </Card>
        <Card withBorder>
          <Group justify="space-between" mb="sm">
            <Title order={2} size="h5">Recent Events</Title>
            <Anchor component={Link} to="/events" size="sm">All events</Anchor>
          </Group>
          {o.recent.length ? (
            <Table.ScrollContainer minWidth={420}>
              <Table highlightOnHover aria-label="Recent events">
                <Table.Tbody>
                  {o.recent.map((e) => (
                    <Table.Tr key={e.id} onClick={e.key ? rowClick(() => setParams({ open: e.key! })) : undefined} style={{ cursor: e.key ? 'pointer' : undefined }}>
                      <Table.Td><EventBadge t={e.type} baseline={e.baseline} /></Table.Td>
                      <Table.Td>
                        {e.key
                          ? <Anchor component={Link} to={openSearch(e.key)} c="inherit" size="sm" lineClamp={1}>{e.title ?? e.summary ?? e.source}</Anchor>
                          : <Text size="sm" lineClamp={1}>{e.title ?? e.summary ?? e.source}</Text>}
                      </Table.Td>
                      <Table.Td><VerdictBadge v={e.verdict} /></Table.Td>
                      <Table.Td ta="right" style={{ whiteSpace: 'nowrap' }}><Text size="xs" c="dimmed" title={when(e.at)}>{ago(e.at)}</Text></Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          ) : <Text size="sm" c="dimmed">No events yet. Each source run records what is new or changed here.</Text>}
        </Card>
      </SimpleGrid>

      <SimpleGrid cols={{ base: 1, lg: 2 }}>
        <Card withBorder>
          <Title order={2} size="h5" mb="sm">Listings by Verdict and Status</Title>
          <Table.ScrollContainer minWidth={360}>
            <Table tabularNums aria-label="Listings by verdict and status">
              <Table.Thead>
                <Table.Tr><Table.Th>Verdict</Table.Th>{statuses.map((s) => <Table.Th key={s} ta="right">{s}</Table.Th>)}</Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {m.verdicts.map((v) => (
                  <Table.Tr key={v}>
                    <Table.Td><VerdictBadge v={v} /></Table.Td>
                    {statuses.map((s) => (
                      <Table.Td key={s} ta="right">
                        <Anchor component={Link} to={`/listings?f=verdict.in.${v}&f=status.in.${s}`} size="sm"
                          aria-label={`${num(o.matrix[v]?.[s] ?? 0)} ${v} ${s} listings`}>{num(o.matrix[v]?.[s] ?? 0)}</Anchor>
                      </Table.Td>
                    ))}
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Card>
        <Card withBorder>
          <Group justify="space-between" mb="sm">
            <Title order={2} size="h5">Next Digest</Title>
            {o.digest && <Text size="xs" c="dimmed">{num(o.digest.events)} events</Text>}
          </Group>
          {o.digest ? (
            <Stack gap="xs"><Text size="sm" fw={500}>{o.digest.subject || 'Nothing to mail; these events are marked read'}</Text>
              <Code block mah={320} style={{ overflow: 'auto', whiteSpace: 'pre-wrap' }}>{o.digest.text}</Code></Stack>
          ) : <Text size="sm" c="dimmed">Nothing pending. The next digest collects events as sources run.</Text>}
        </Card>
      </SimpleGrid>
      <ListingDrawer k={open} onClose={() => setParams({})} />
    </Stack>
  )
}
