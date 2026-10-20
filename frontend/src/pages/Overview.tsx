import { Alert, Anchor, Card, Center, Code, Group, Loader, SimpleGrid, Stack, Table, Text, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'

import { api, type Overview } from '../api'
import { EventBadge, StatusBadge, VerdictBadge } from '../components/Badges'
import { ListingDrawer } from '../components/ListingDrawer'
import { ago, money, range } from '../format'
import { useMeta } from '../hooks'

function Stat({ label, value, hint, to }: { label: string; value: number | string; hint?: string; to?: string }) {
  return (
    <Card withBorder padding="md">
      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>{label}</Text>
      <Title order={2}>{to ? <Anchor component={Link} to={to} inherit>{value}</Anchor> : value}</Title>
      {hint && <Text size="xs" c="dimmed">{hint}</Text>}
    </Card>
  )
}

export function OverviewPage() {
  const q = useQuery({ queryKey: ['overview'], queryFn: () => api.get<Overview>('/api/overview'), refetchInterval: 60_000 })
  const meta = useMeta().data
  const [params, setParams] = useSearchParams()
  const open = params.get('open')
  if (q.isLoading || !meta) return <Center h={200}><Loader /></Center>
  if (q.error) return <Alert color="red" title="The store is not reachable">{String((q.error as Error).message)}</Alert>
  const o = q.data!
  const p = meta.profile
  return (
    <Stack gap="lg">
      <Stack gap={2}>
        <Title order={3}>Overview</Title>
        <Text size="sm" c="dimmed">
          {p.household} person · {money(p.income)} · {p.sizes.join(', ')}{p.excluded_boroughs.length ? ` · not ${p.excluded_boroughs.join(', ')}` : ''}
          {' · '}{o.total.toLocaleString()} listings from {o.n_sources} sources
        </Text>
      </Stack>
      {o.failing.length > 0 && (
        <Alert color="red" title={`${o.failing.length} source${o.failing.length > 1 ? 's' : ''} failing`}>
          {o.failing.join(', ')} · <Anchor component={Link} to="/sources">Sources</Anchor>
        </Alert>
      )}
      <SimpleGrid cols={{ base: 2, sm: 3, lg: 6 }}>
        <Stat label="Eligible & open" value={o.eligible_open} hint={`${o.eligible_live} incl. waitlist / upcoming`}
          to="/listings?f=verdict.in.eligible&f=status.in.open" />
        <Stat label="New 24h" value={o.new_24h} hint={`${o.new_7d} in 7 days${o.baseline_24h ? ` · ${o.baseline_24h} first-run` : ''}`} />
        <Stat label="Changed 24h" value={o.changed_24h} hint={`${o.changed_7d} in 7 days`} />
        <Stat label="Digest pending" value={o.pending} hint={`last sent ${ago(o.last_digest)}`} />
        <Stat label="Sources" value={o.n_sources} hint={o.failing.length ? `${o.failing.length} failing` : 'all healthy'} to="/sources" />
        <Stat label="Listings" value={o.total.toLocaleString()} to="/listings" />
      </SimpleGrid>

      <SimpleGrid cols={{ base: 1, lg: 2 }}>
        <Card withBorder>
          <Title order={5} mb="sm">Eligible now</Title>
          {o.eligible_now.length ? (
            <Table highlightOnHover>
              <Table.Tbody>
                {o.eligible_now.map((x) => (
                  <Table.Tr key={x.key} onClick={() => setParams({ open: x.key })} style={{ cursor: 'pointer' }}>
                    <Table.Td><Text size="sm" fw={500} lineClamp={1}>{x.title}</Text><Text size="xs" c="dimmed">{x.borough ?? '—'} · {x.source}</Text></Table.Td>
                    <Table.Td ta="right" style={{ whiteSpace: 'nowrap' }}>{range(x.rent_min, x.rent_max, money)}</Table.Td>
                    <Table.Td><StatusBadge s={x.status} /></Table.Td>
                    <Table.Td ta="right" style={{ whiteSpace: 'nowrap' }}><Text size="xs" c="dimmed">{ago(x.last_changed)}</Text></Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          ) : <Text size="sm" c="dimmed">Nothing eligible is open right now.</Text>}
        </Card>
        <Card withBorder>
          <Title order={5} mb="sm">Recent events</Title>
          <Table highlightOnHover>
            <Table.Tbody>
              {o.recent.map((e) => (
                <Table.Tr key={e.id} onClick={() => e.key && setParams({ open: e.key })} style={{ cursor: e.key ? 'pointer' : undefined }}>
                  <Table.Td><EventBadge t={e.type} baseline={e.baseline} /></Table.Td>
                  <Table.Td><Text size="sm" lineClamp={1}>{e.title ?? e.summary}</Text></Table.Td>
                  <Table.Td><VerdictBadge v={e.verdict} /></Table.Td>
                  <Table.Td ta="right" style={{ whiteSpace: 'nowrap' }}><Text size="xs" c="dimmed">{ago(e.at)}</Text></Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          <Anchor component={Link} to="/events" size="sm">All events</Anchor>
        </Card>
      </SimpleGrid>

      <SimpleGrid cols={{ base: 1, lg: 2 }}>
        <Card withBorder>
          <Title order={5} mb="sm">Listings by verdict and status</Title>
          <Table>
            <Table.Thead><Table.Tr><Table.Th />{meta.statuses.filter((s) => s !== 'gone').map((s) => <Table.Th key={s} ta="right">{s}</Table.Th>)}</Table.Tr></Table.Thead>
            <Table.Tbody>
              {meta.verdicts.map((v) => (
                <Table.Tr key={v}>
                  <Table.Td><VerdictBadge v={v} /></Table.Td>
                  {meta.statuses.filter((s) => s !== 'gone').map((s) => (
                    <Table.Td key={s} ta="right">
                      <Anchor component={Link} to={`/listings?f=verdict.in.${v}&f=status.in.${s}`} size="sm">{o.matrix[v]?.[s] ?? 0}</Anchor>
                    </Table.Td>
                  ))}
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Card>
        <Card withBorder>
          <Group justify="space-between" mb="sm"><Title order={5}>Next digest</Title>{o.digest && <Text size="xs" c="dimmed">{o.digest.events} events</Text>}</Group>
          {o.digest ? (
            <Stack gap="xs"><Text size="sm" fw={500}>{o.digest.subject || '(nothing to mail; marked read)'}</Text>
              <Code block mah={320} style={{ overflow: 'auto', whiteSpace: 'pre-wrap' }}>{o.digest.text}</Code></Stack>
          ) : <Text size="sm" c="dimmed">Nothing pending.</Text>}
        </Card>
      </SimpleGrid>
      <ListingDrawer k={open} onClose={() => setParams({})} />
    </Stack>
  )
}
