import { Alert, Badge, Card, Code, Group, SimpleGrid, Stack, Table, Text, Title, VisuallyHidden } from '@mantine/core'
import { IconAlertTriangle } from '@tabler/icons-react'
import { useQuery } from '@tanstack/react-query'

import { request } from '../api'
import { graphql } from '../gql'
import { Dot } from '../components/Badges'
import { PageHeader } from '../components/PageHeader'
import { Loading, LoadError } from '../components/State'
import { ago, num, when } from '../format'
import { useTitle } from '../hooks'

const FleetDoc = graphql(`
  query Fleet {
    fleet {
      available workers { name queues concurrency started last_seen age doing } queues { queue status count }
      failures { id queue_name task_name args attempts at error } hosts { name last_run runs_24h fails_24h sources }
    }
  }
`)

const QUEUE_STATES = ['todo', 'doing', 'succeeded', 'failed']

/** A table body's single "nothing here" row. */
const Empty = ({ cols, children }: { cols: number; children: React.ReactNode }) => (
  <Table.Tr><Table.Td colSpan={cols}><Text size="sm" c="dimmed">{children}</Text></Table.Td></Table.Tr>
)

export function FleetPage() {
  useTitle('Fleet')
  const q = useQuery({ queryKey: ['fleet'], queryFn: () => request(FleetDoc).then((d) => d.fleet), refetchInterval: 15_000 })
  if (q.error) return <LoadError what="the fleet" error={q.error} retry={() => q.refetch()} />
  if (q.isLoading) return <Loading rows={10} label="Loading the fleet…" />
  const f = q.data!
  const queues = new Map<string, Record<string, number>>()
  for (const c of f.queues) queues.set(c.queue, { ...queues.get(c.queue), [c.status]: c.count })
  return (
    <Stack gap="lg">
      <PageHeader title="Fleet" description={<>Procrastinate workers and job queues on the shared Postgres{f.available ? ` · ${num(f.workers.length)} workers` : ''}</>} />
      {!f.available && (
        <Alert color="yellow" variant="light" icon={<IconAlertTriangle size={16} aria-hidden />} title="The job queue isn’t set up yet">
          Procrastinate’s tables don’t exist in this database. Start a worker and it creates them.
        </Alert>
      )}
      <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="md">
        <Card>
          <Title order={2} mb="sm">Workers</Title>
          <Table.ScrollContainer minWidth={480}>
            <Table tabularNums fz="sm" verticalSpacing={6} aria-label="Workers">
              <Table.Thead><Table.Tr><Table.Th>Host</Table.Th><Table.Th>Queues</Table.Th><Table.Th ta="right">Heartbeat</Table.Th><Table.Th>Running</Table.Th></Table.Tr></Table.Thead>
              <Table.Tbody>
                {f.workers.length === 0 && <Empty cols={4}>No worker has checked in.</Empty>}
                {f.workers.map((w) => {
                  const health = w.age < 90 ? 'live' : w.age < 600 ? 'late' : 'stale'
                  return (
                    <Table.Tr key={w.name} opacity={health === 'stale' ? 0.55 : 1}>
                      <Table.Td>
                        <Group gap={6} wrap="nowrap">
                          <Dot color={health === 'live' ? 'green' : health === 'late' ? 'yellow' : 'red'} />
                          <VisuallyHidden>{health}</VisuallyHidden>
                          <Text size="sm" fw={500} translate="no">{w.name}</Text>
                        </Group>
                      </Table.Td>
                      <Table.Td><span translate="no">{w.queues.join(', ')}</span> · {num(w.concurrency)} slots</Table.Td>
                      <Table.Td ta="right" title={when(w.last_seen)}>{ago(w.last_seen)}</Table.Td>
                      <Table.Td>
                        {w.doing.length
                          ? <Group gap={4}>{w.doing.map((d) => <Badge key={d} translate="no">{d}</Badge>)}</Group>
                          : <Text size="sm" c="dimmed">idle</Text>}
                      </Table.Td>
                    </Table.Tr>
                  )
                })}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Card>
        <Card>
          <Title order={2} mb="sm">Queues</Title>
          <Table.ScrollContainer minWidth={360}>
            <Table tabularNums fz="sm" verticalSpacing={6} aria-label="Queues">
              <Table.Thead><Table.Tr><Table.Th>Queue</Table.Th>{QUEUE_STATES.map((s) => <Table.Th key={s} ta="right" tt="capitalize">{s}</Table.Th>)}</Table.Tr></Table.Thead>
              <Table.Tbody>
                {queues.size === 0 && <Empty cols={5}>No jobs have been queued.</Empty>}
                {[...queues].map(([name, st]) => (
                  <Table.Tr key={name}><Table.Td translate="no">{name}</Table.Td>{QUEUE_STATES.map((s) => <Table.Td key={s} ta="right">{num(st[s] ?? 0)}</Table.Td>)}</Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Card>
      </SimpleGrid>
      <Card>
        <Title order={2} mb="sm">Hosts (From Runs)</Title>
        <Table.ScrollContainer minWidth={480}>
          <Table tabularNums fz="sm" verticalSpacing={6} aria-label="Hosts">
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
        <Card>
          <Title order={2} mb="sm">Failed Jobs</Title>
          <Stack gap="md">
            {f.failures.map((j) => (
              <Stack key={j.id} gap={6}>
                <Group gap="xs">
                  <Badge color="red" translate="no">{j.task_name}</Badge>
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
