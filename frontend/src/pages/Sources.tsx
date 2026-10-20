import { Alert, Anchor, Badge, Center, Group, Loader, Stack, Table, Text, Title, Tooltip } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api, type Source } from '../api'
import { ago, duration } from '../format'

const HEALTH = { ok: 'green', failing: 'red', never: 'gray' } as const

export function SourcesPage() {
  const q = useQuery({ queryKey: ['sources'], queryFn: () => api.get<Source[]>('/api/sources'), refetchInterval: 60_000 })
  if (q.isLoading) return <Center h={200}><Loader /></Center>
  if (q.error) return <Alert color="red">{String((q.error as Error).message)}</Alert>
  const rows = [...q.data!].sort((a, b) => (a.health === 'failing' ? 0 : 1) - (b.health === 'failing' ? 0 : 1) || a.source.localeCompare(b.source))
  return (
    <Stack gap="sm">
      <Title order={3}>Sources</Title>
      <Text size="sm" c="dimmed">{rows.length} sources · {rows.filter((s) => s.health === 'failing').length} failing</Text>
      <Table.ScrollContainer minWidth={1100}>
        <Table stickyHeader highlightOnHover verticalSpacing={6}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Source</Table.Th><Table.Th>Health</Table.Th><Table.Th ta="right">Every</Table.Th><Table.Th ta="right">Last ok</Table.Th>
              <Table.Th ta="right">Next</Table.Th><Table.Th ta="right">Took</Table.Th><Table.Th ta="right">Listings</Table.Th>
              <Table.Th ta="right">Eligible</Table.Th><Table.Th ta="right">Possible</Table.Th><Table.Th>Ran on (7d)</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((s) => (
              <Table.Tr key={s.source}>
                <Table.Td>
                  <Text size="sm" fw={500}>{s.url ? <Anchor href={s.url} target="_blank" rel="noopener noreferrer" inherit>{s.source}</Anchor> : s.source}</Text>
                  {s.title && <Text size="xs" c="dimmed" lineClamp={1}>{s.title}</Text>}
                </Table.Td>
                <Table.Td>
                  <Tooltip label={s.last_error} disabled={!s.last_error} multiline w={420} withinPortal>
                    <Badge color={HEALTH[s.health]} variant="light">{s.health}{s.consecutive_failures ? ` ×${s.consecutive_failures}` : ''}</Badge>
                  </Tooltip>
                </Table.Td>
                <Table.Td ta="right">{s.every_hours == null ? '—' : `${s.every_hours}h`}</Table.Td>
                <Table.Td ta="right">{ago(s.last_ok)}</Table.Td>
                <Table.Td ta="right">{ago(s.next_due)}</Table.Td>
                <Table.Td ta="right">{duration(s.last_duration)}</Table.Td>
                <Table.Td ta="right"><Anchor component={Link} to={`/listings?f=source.in.${encodeURIComponent(s.source)}`} size="sm">{s.listings}</Anchor></Table.Td>
                <Table.Td ta="right">{s.eligible || '—'}</Table.Td>
                <Table.Td ta="right">{s.possible || '—'}</Table.Td>
                <Table.Td><Group gap={4}>{s.workers.map((w) => <Badge key={w} variant="default" size="sm">{w}</Badge>)}</Group></Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Stack>
  )
}
