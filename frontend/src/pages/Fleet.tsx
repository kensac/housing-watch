import { Alert, Badge, Card, Center, Code, Group, Loader, SimpleGrid, Stack, Table, Text, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'

import { api, type Fleet } from '../api'
import { ago } from '../format'

export function FleetPage() {
  const q = useQuery({ queryKey: ['fleet'], queryFn: () => api.get<Fleet>('/api/fleet'), refetchInterval: 15_000 })
  if (q.isLoading) return <Center h={200}><Loader /></Center>
  if (q.error) return <Alert color="red">{String((q.error as Error).message)}</Alert>
  const f = q.data!
  return (
    <Stack gap="lg">
      <Stack gap={2}>
        <Title order={3}>Fleet</Title>
        <Text size="sm" c="dimmed">Procrastinate workers and job queues on the shared Postgres{f.available ? ` · ${f.workers.length} workers` : ''}</Text>
      </Stack>
      {!f.available && <Alert color="yellow" title="The job queue isn't set up yet">Procrastinate's tables don't exist in this database. A worker creates them when it starts.</Alert>}
      <SimpleGrid cols={{ base: 1, lg: 2 }}>
        <Card withBorder>
          <Title order={5} mb="sm">Workers</Title>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>Host</Table.Th><Table.Th>Queues</Table.Th><Table.Th ta="right">Heartbeat</Table.Th><Table.Th>Running</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>
              {f.workers.map((w) => (
                <Table.Tr key={w.name} opacity={w.age > 600 ? 0.5 : 1}>
                  <Table.Td><Group gap={6}><Badge circle size="xs" color={w.age < 90 ? 'green' : w.age < 600 ? 'yellow' : 'red'}> </Badge><Text size="sm" fw={500}>{w.name}</Text></Group></Table.Td>
                  <Table.Td><Text size="sm">{w.queues.join(', ')} · {w.concurrency} slots</Text></Table.Td>
                  <Table.Td ta="right"><Text size="sm">{ago(w.last_seen)}</Text></Table.Td>
                  <Table.Td>{w.doing.length ? <Group gap={4}>{w.doing.map((d) => <Badge key={d} variant="light" size="sm">{d}</Badge>)}</Group> : <Text size="sm" c="dimmed">idle</Text>}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Card>
        <Card withBorder>
          <Title order={5} mb="sm">Queues</Title>
          <Table>
            <Table.Thead><Table.Tr><Table.Th>Queue</Table.Th>{['todo', 'doing', 'succeeded', 'failed'].map((s) => <Table.Th key={s} ta="right">{s}</Table.Th>)}</Table.Tr></Table.Thead>
            <Table.Tbody>
              {Object.entries(f.queues).map(([name, st]) => (
                <Table.Tr key={name}><Table.Td>{name}</Table.Td>{['todo', 'doing', 'succeeded', 'failed'].map((s) => <Table.Td key={s} ta="right">{st[s] ?? 0}</Table.Td>)}</Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Card>
      </SimpleGrid>
      <Card withBorder>
        <Title order={5} mb="sm">Hosts (from runs)</Title>
        <Table>
          <Table.Thead><Table.Tr><Table.Th>Host</Table.Th><Table.Th ta="right">Last run</Table.Th><Table.Th ta="right">Runs 24h</Table.Th><Table.Th ta="right">Failed 24h</Table.Th><Table.Th ta="right">Sources</Table.Th></Table.Tr></Table.Thead>
          <Table.Tbody>
            {f.hosts.map((h) => (
              <Table.Tr key={h.name}><Table.Td>{h.name}</Table.Td><Table.Td ta="right">{ago(h.last_run)}</Table.Td><Table.Td ta="right">{h.runs_24h}</Table.Td>
                <Table.Td ta="right">{h.fails_24h || '—'}</Table.Td><Table.Td ta="right">{h.sources}</Table.Td></Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Card>
      {f.failures.length > 0 && (
        <Card withBorder>
          <Title order={5} mb="sm">Failed jobs</Title>
          <Stack gap="xs">
            {f.failures.map((j) => (
              <Stack key={j.id} gap={2}>
                <Group gap="xs"><Badge color="red" variant="light">{j.task_name}</Badge><Text size="sm">{String(j.args?.source ?? '')}</Text>
                  <Text size="xs" c="dimmed">{j.queue_name} · {j.attempts} attempts · {ago(j.at)}</Text></Group>
                {j.error && <Code block style={{ whiteSpace: 'pre-wrap' }}>{j.error}</Code>}
              </Stack>
            ))}
          </Stack>
        </Card>
      )}
    </Stack>
  )
}
