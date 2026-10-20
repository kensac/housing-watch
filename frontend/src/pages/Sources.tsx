import { Anchor, Badge, Group, Stack, Table, Text, Title, Tooltip } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api, type Source } from '../api'
import { Loading, LoadError } from '../components/State'
import { ago, duration, num, when } from '../format'
import { useMotion, useTitle } from '../hooks'

const HEALTH = { ok: 'green', failing: 'red', never: 'gray' } as const

export function SourcesPage() {
  useTitle('Sources')
  const motion = useMotion()
  const q = useQuery({ queryKey: ['sources'], queryFn: () => api.get<Source[]>('/api/sources'), refetchInterval: 60_000 })
  if (q.error) return <LoadError what="sources" error={q.error} retry={() => q.refetch()} />
  if (q.isLoading) return <Loading rows={12} label="Loading sources…" />
  const rows = [...q.data!].sort((a, b) => (a.health === 'failing' ? 0 : 1) - (b.health === 'failing' ? 0 : 1) || a.source.localeCompare(b.source))
  const failing = rows.filter((s) => s.health === 'failing').length
  return (
    <Stack gap="sm">
      <Title order={1} size="h3">Sources</Title>
      <Text size="sm" c="dimmed">{num(rows.length)} sources · {failing ? `${num(failing)} failing, listed first` : 'none failing'}</Text>
      <Table.ScrollContainer minWidth={1100}>
        <Table highlightOnHover tabularNums verticalSpacing={6} aria-label="Sources">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Source</Table.Th><Table.Th>Health</Table.Th><Table.Th ta="right">Every</Table.Th><Table.Th ta="right">Last OK</Table.Th>
              <Table.Th ta="right">Next Run</Table.Th><Table.Th ta="right">Took</Table.Th><Table.Th ta="right">Listings</Table.Th>
              <Table.Th ta="right">Eligible</Table.Th><Table.Th ta="right">Possible</Table.Th><Table.Th>Ran On (7d)</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.length === 0 && (
              <Table.Tr><Table.Td colSpan={10}><Text size="sm" c="dimmed" ta="center" py="md">No sources are configured on the server.</Text></Table.Td></Table.Tr>
            )}
            {rows.map((s) => (
              <Table.Tr key={s.source}>
                <Table.Td maw={320}>
                  <Text size="sm" fw={500} translate="no">{s.url ? <Anchor href={s.url} target="_blank" rel="noopener noreferrer" inherit>{s.source}</Anchor> : s.source}</Text>
                  {s.title && <Text size="xs" c="dimmed" lineClamp={1}>{s.title}</Text>}
                </Table.Td>
                <Table.Td>
                  <Tooltip label={s.last_error} disabled={!s.last_error} multiline w={420} withinPortal transitionProps={motion}
                    events={{ hover: true, focus: true, touch: true }}>
                    <Badge color={HEALTH[s.health]} variant="light" tabIndex={s.last_error ? 0 : undefined}
                      aria-label={s.last_error ? `${s.health}, last error: ${s.last_error}` : undefined}>
                      {s.health}{s.consecutive_failures ? ` ×${s.consecutive_failures}` : ''}
                    </Badge>
                  </Tooltip>
                </Table.Td>
                <Table.Td ta="right">{s.every_hours == null ? '—' : `${num(s.every_hours)}h`}</Table.Td>
                <Table.Td ta="right" title={when(s.last_ok)}>{ago(s.last_ok)}</Table.Td>
                <Table.Td ta="right" title={when(s.next_due)}>{s.next_due ? ago(s.next_due) : '—'}</Table.Td>
                <Table.Td ta="right">{duration(s.last_duration)}</Table.Td>
                <Table.Td ta="right"><Anchor component={Link} to={`/listings?f=source.in.${encodeURIComponent(s.source)}`} size="sm"
                  aria-label={`${num(s.listings)} listings from ${s.source}`}>{num(s.listings)}</Anchor></Table.Td>
                <Table.Td ta="right">{s.eligible ? num(s.eligible) : '—'}</Table.Td>
                <Table.Td ta="right">{s.possible ? num(s.possible) : '—'}</Table.Td>
                <Table.Td><Group gap={4}>{s.workers.length ? s.workers.map((w) => <Badge key={w} variant="default" size="sm" translate="no">{w}</Badge>) : <Text size="sm" c="dimmed">—</Text>}</Group></Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Stack>
  )
}
