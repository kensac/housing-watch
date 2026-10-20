import { Alert, Badge, Card, Code, Group, SimpleGrid, Stack, Table, Text, Title, VisuallyHidden } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'

import { api, type Fleet } from '../api'
import { Loading, LoadError } from '../components/State'
import { ago, num, when } from '../format'
import { useTitle } from '../hooks'

const QUEUE_STATES = ['todo', 'doing', 'succeeded', 'failed']

/** A table body's single "nothing here" row. */
const Empty = ({ cols, children }: { cols: number; children: React.ReactNode }) => (
  <Table.Tr><Table.Td colSpan={cols}><Text size="sm" c="dimmed">{children}</Text></Table.Td></Table.Tr>
)

export function FleetPage() {
  useTitle('Fleet')
  const q = useQuery({ queryKey: ['fleet'], queryFn: () => api.get<Fleet>('/api/fleet'), refetchInterval: 15_000 })
  if (q.error) return <LoadError what="the fleet" error={q.error} retry={() => q.refetch()} />
  if (q.isLoading) return <Loading rows={10} label="Loading the fleet…" />
  const f = q.data!
  const queues = Object.entries(f.queues)
  return (
    <Stack gap="lg">
      <Stack gap={2}>
        <Title order={1} size="h3">Fleet</Title>
        <Text size="sm" c="dimmed">Procrastinate workers and job queues on the shared Postgres{f.available ? ` · ${num(f.workers.length)} workers` : ''}</Text>
      </Stack>
      {!f.available && (
        <Alert color="yellow" variant="light" title="The job queue isn’t set up yet">
          Procrastinate’s tables don’t exist in this database. Start a worker and it creates them.
        </Alert>
      )}
      <SimpleGrid cols={{ base: 1, lg: 2 }}>
        <Card withBorder>
          <Title order={2} size="h5" mb="sm">Workers</Title>
          <Table.ScrollContainer minWidth={480}>
            <Table tabularNums aria-label="Workers">
              <Table.Thead><Table.Tr><Table.Th>Host</Table.Th><Table.Th>Queues</Table.Th><Table.Th ta="right">Heartbeat</Table.Th><Table.Th>Running</Table.Th></Table.Tr></Table.Thead>
              <Table.Tbody>
                {f.workers.length === 0 && <Empty cols={4}>No worker has checked in.</Empty>}
                {f.workers.map((w) => {
                  const health = w.age < 90 ? 'live' : w.age < 600 ? 'late' : 'stale'
                  return (
                    <Table.Tr key={w.name} opacity={health === 'stale' ? 0.5 : 1}>
                      <Table.Td>
                        <Group gap={6} wrap="nowrap">
                          <Badge circle size="xs" color={health === 'live' ? 'green' : health === 'late' ? 'yellow' : 'red'} aria-hidden> </Badge>
                          <VisuallyHidden>{health}</VisuallyHidden>
                          <Text size="sm" fw={500} translate="no">{w.name}</Text>
                        </Group>
                      </Table.Td>
                      <Table.Td><Text size="sm"><span translate="no">{w.queues.join(', ')}</span> · {num(w.concurrency)} slots</Text></Table.Td>
                      <Table.Td ta="right"><Text size="sm" title={when(w.last_seen)}>{ago(w.last_seen)}</Text></Table.Td>
                      <Table.Td>
                        {w.doing.length
                          ? <Group gap={4}>{w.doing.map((d) => <Badge key={d} variant="light" size="sm" translate="no">{d}</Badge>)}</Group>
                          : <Text size="sm" c="dimmed">idle</Text>}
                      </Table.Td>
                    </Table.Tr>
                  )
                })}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Card>
        <Card withBorder>
          <Title order={2} size="h5" mb="sm">Queues</Title>
          <Table.ScrollContainer minWidth={360}>
            <Table tabularNums aria-label="Queues">
              <Table.Thead><Table.Tr><Table.Th>Queue</Table.Th>{QUEUE_STATES.map((s) => <Table.Th key={s} ta="right">{s}</Table.Th>)}</Table.Tr></Table.Thead>
              <Table.Tbody>
                {queues.length === 0 && <Empty cols={5}>No jobs have been queued.</Empty>}
                {queues.map(([name, st]) => (
                  <Table.Tr key={name}><Table.Td translate="no">{name}</Table.Td>{QUEUE_STATES.map((s) => <Table.Td key={s} ta="right">{num(st[s] ?? 0)}</Table.Td>)}</Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Card>
      </SimpleGrid>
      <Card withBorder>
        <Title order={2} size="h5" mb="sm">Hosts (From Runs)</Title>
        <Table.ScrollContainer minWidth={480}>
          <Table tabularNums aria-label="Hosts">
            <Table.Thead><Table.Tr><Table.Th>Host</Table.Th><Table.Th ta="right">Last Run</Table.Th><Table.Th ta="right">Runs 24h</Table.Th><Table.Th ta="right">Failed 24h</Table.Th><Table.Th ta="right">Sources</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>
              {f.hosts.length === 0 && <Empty cols={5}>No source runs recorded yet.</Empty>}
              {f.hosts.map((h) => (
                <Table.Tr key={h.name}>
                  <Table.Td translate="no">{h.name}</Table.Td><Table.Td ta="right" title={when(h.last_run)}>{ago(h.last_run)}</Table.Td>
                  <Table.Td ta="right">{num(h.runs_24h)}</Table.Td><Table.Td ta="right">{h.fails_24h ? num(h.fails_24h) : '—'}</Table.Td>
                  <Table.Td ta="right">{num(h.sources)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Card>
      {f.failures.length > 0 && (
        <Card withBorder>
          <Title order={2} size="h5" mb="sm">Failed Jobs</Title>
          <Stack gap="sm">
            {f.failures.map((j) => (
              <Stack key={j.id} gap={4}>
                <Group gap="xs">
                  <Badge color="red" variant="light" translate="no">{j.task_name}</Badge>
                  <Text size="sm" translate="no">{String(j.args?.source ?? '')}</Text>
                  <Text size="xs" c="dimmed">{j.queue_name} · {num(j.attempts)} attempts · <span title={when(j.at)}>{ago(j.at)}</span></Text>
                </Group>
                {j.error && <Code block translate="no" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{j.error}</Code>}
              </Stack>
            ))}
          </Stack>
        </Card>
      )}
    </Stack>
  )
}
