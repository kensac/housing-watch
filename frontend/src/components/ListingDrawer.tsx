import { Alert, Anchor, Badge, Button, Center, Code, Drawer, Group, Loader, SimpleGrid, Stack, Table, Text, Timeline, Title } from '@mantine/core'
import { IconDownload, IconExternalLink } from '@tabler/icons-react'
import { useQuery } from '@tanstack/react-query'

import { api, qs, type Applications, type Detail } from '../api'
import { money, when } from '../format'
import { EventBadge, StatusBadge, VerdictBadge } from './Badges'

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return <Stack gap={0}><Text size="xs" c="dimmed">{label}</Text><Text size="sm">{children}</Text></Stack>
}

function ApplicationsPanel({ k }: { k: string }) {
  const q = useQuery({ queryKey: ['applications', k], queryFn: () => api.get<Applications>(`/api/listing/applications${qs({ key: k })}`) })
  if (q.isLoading) return <Group gap="xs"><Loader size="xs" /><Text size="sm" c="dimmed">Checking the application PDF…</Text></Group>
  if (!q.data?.pdfs.length) return null
  return (
    <Stack gap="xs">
      {q.data.pdfs.map((p) => (
        <Group key={p.n} justify="space-between" wrap="nowrap">
          <Text size="sm" fw={500} truncate>{p.form ?? (p.error ? 'Link failed' : 'Not a fillable form we know')}</Text>
          <Group gap="xs" wrap="nowrap">
            {p.form && q.data.ready && (
              <Button size="xs" component="a" href={`/api/listing/application${qs({ key: k, n: p.n })}`} leftSection={<IconDownload size={14} />}>
                Download filled
              </Button>
            )}
            <Button size="xs" variant="default" component="a" href={p.url} target="_blank" rel="noopener noreferrer">Blank PDF</Button>
          </Group>
        </Group>
      ))}
      <Text size="xs" c="dimmed">
        {q.data.ready ? 'Filled from the applicant file, signed and dated today. The SSN is left blank.' : 'Set HW_APPLICANT_FILE to fill known forms.'}
      </Text>
    </Stack>
  )
}

/** One listing: facts, every unit with your income band, application PDFs, notes and history. */
export function ListingDrawer({ k, onClose }: { k: string | null; onClose: () => void }) {
  const q = useQuery({ queryKey: ['listing', k], queryFn: () => api.get<Detail>(`/api/listing${qs({ key: k })}`), enabled: Boolean(k) })
  const d = q.data
  const x = d?.summary
  return (
    <Drawer opened={Boolean(k)} onClose={onClose} position="right" size="xl" title={x ? <Title order={4}>{x.title}</Title> : 'Listing'}>
      {q.isLoading && <Center h={200}><Loader /></Center>}
      {q.error && <Alert color="red">{String((q.error as Error).message)}</Alert>}
      {d && x && (
        <Stack gap="lg">
          <Group gap="xs">
            <VerdictBadge v={x.verdict} />
            <Text size="sm" c="dimmed">{x.verdict === 'eligible' ? `${x.eligible_units} of ${x.units} units fit the profile` : x.reason}</Text>
            <StatusBadge s={x.status} />
            <Badge variant="default">{x.source} · {x.kind}</Badge>
            {x.url && <Button size="xs" variant="light" component="a" href={x.url} target="_blank" rel="noopener noreferrer"
              rightSection={<IconExternalLink size={14} />}>Open source</Button>}
          </Group>
          <SimpleGrid cols={{ base: 2, sm: 3 }}>
            <Fact label="Address">{x.address ?? '—'}</Fact>
            <Fact label="Borough">{x.borough ?? 'unknown'}</Fact>
            <Fact label="Deadline">{x.deadline ?? '—'}</Fact>
            <Fact label="First seen">{when(x.first_seen)}</Fact>
            <Fact label="Last changed">{when(x.last_changed)}</Fact>
            <Fact label="Last seen">{when(d.last_seen)}</Fact>
            {x.gone_since && <Fact label="Removed">{when(x.gone_since)}</Fact>}
            {x.senior_only && <Fact label="Restriction">senior only</Fact>}
          </SimpleGrid>

          <Stack gap="xs">
            <Title order={5}>Units ({d.units.length})</Title>
            {d.units.length ? (
              <Table.ScrollContainer minWidth={560}>
                <Table striped>
                  <Table.Thead><Table.Tr><Table.Th>Unit</Table.Th><Table.Th ta="right">Rent</Table.Th><Table.Th ta="right">AMI</Table.Th><Table.Th>Income band</Table.Th><Table.Th>Verdict</Table.Th></Table.Tr></Table.Thead>
                  <Table.Tbody>
                    {d.units.map((u, i) => (
                      <Table.Tr key={i} opacity={u.verdict === 'excluded' ? 0.6 : 1}>
                        <Table.Td>
                          <Group gap={4}>
                            <Badge variant="default">{u.size}</Badge>{u.unit_no && <Text size="sm">#{u.unit_no}</Text>}
                            {u.set_aside && <Badge color="orange" variant="light">{u.set_aside}</Badge>}
                            {u.available === false && <Badge variant="outline">rented</Badge>}
                            {u.chart_year && <Badge variant="outline" color={u.reason.includes('limits') ? 'orange' : 'gray'}>{u.chart_year} chart</Badge>}
                          </Group>
                        </Table.Td>
                        <Table.Td ta="right">{money(u.rent)}</Table.Td>
                        <Table.Td ta="right">{u.ami ? `${u.ami}%` : '—'}</Table.Td>
                        <Table.Td>
                          {u.bands.length ? u.bands.map(([h, lo, hi]) => (
                            <Text key={h} size="xs" fw={h === d.household ? 700 : 400} c={h === d.household ? undefined : 'dimmed'}>
                              {h}p {lo == null ? '?' : money(lo)}–{hi == null ? '?' : money(hi)}
                            </Text>
                          )) : <Text size="xs" c="dimmed">no table</Text>}
                        </Table.Td>
                        <Table.Td><Group gap={4} wrap="nowrap"><VerdictBadge v={u.verdict} />{(u.verdict !== 'eligible' || u.reason.includes('limits')) && <Text size="xs" c="dimmed">{u.reason}</Text>}</Group></Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            ) : <Text size="sm" c="dimmed">The source publishes no unit details for this listing.</Text>}
          </Stack>

          {d.pdfs.length > 0 && <Stack gap="xs"><Title order={5}>Application</Title><ApplicationsPanel k={x.key} /></Stack>}

          {d.notes && (
            <Stack gap="xs"><Title order={5}>Notes</Title>
              <Code block style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{d.notes}</Code></Stack>
          )}

          <Stack gap="xs">
            <Title order={5}>History</Title>
            {d.events.length ? (
              <Timeline bulletSize={14} lineWidth={2}>
                {d.events.map((e) => (
                  <Timeline.Item key={e.id} title={<Group gap="xs"><EventBadge t={e.type} baseline={e.baseline} /><Text size="xs" c="dimmed">{when(e.at)}{e.notified ? ' · mailed' : ''}</Text></Group>}>
                    {e.summary && <Text size="sm" c="dimmed">{e.summary}</Text>}
                  </Timeline.Item>
                ))}
              </Timeline>
            ) : <Text size="sm" c="dimmed">No events recorded for this listing.</Text>}
          </Stack>
          <Text size="xs" c="dimmed">key <Anchor size="xs" component="span">{x.key}</Anchor></Text>
        </Stack>
      )}
    </Drawer>
  )
}
