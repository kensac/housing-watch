import { Badge, Button, Code, Drawer, Group, Loader, SimpleGrid, Stack, Table, Text, Timeline, Title } from '@mantine/core'
import { IconDownload, IconExternalLink } from '@tabler/icons-react'
import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { api, qs, type Applications, type Detail } from '../api'
import { commute, date, money, pct, when } from '../format'
import { useMotion } from '../hooks'
import { EventBadge, SizeBadge, StatusBadge, VerdictBadge } from './Badges'
import { Loading, LoadError } from './State'
import { TrackerPanel } from './TrackerPanel'

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return <Stack gap={2} miw={0}><Text size="xs" c="dimmed">{label}</Text><Text size="sm" style={{ overflowWrap: 'anywhere' }}>{children}</Text></Stack>
}

/** A titled part of the drawer; sections are separated by space alone. */
function Section({ title, right, children }: { title: ReactNode; right?: ReactNode; children: ReactNode }) {
  return (
    <Stack gap="xs">
      <Group justify="space-between" gap="xs"><Title order={2}>{title}</Title>{right}</Group>
      {children}
    </Stack>
  )
}

function ApplicationsPanel({ k }: { k: string }) {
  const q = useQuery({ queryKey: ['applications', k], queryFn: () => api.get<Applications>(`/api/listing/applications${qs({ key: k })}`) })
  if (q.isLoading) return <Group gap="xs" role="status"><Loader size="xs" /><Text size="sm" c="dimmed">Checking the application PDF…</Text></Group>
  if (q.error) return <LoadError what="the application PDFs" error={q.error} retry={() => q.refetch()} />
  if (!q.data?.pdfs.length) return <Text size="sm" c="dimmed">No application PDF is linked from this listing.</Text>
  return (
    <Stack gap="xs">
      {q.data.pdfs.map((p) => (
        <Group key={p.n} justify="space-between" gap="xs">
          <Text size="sm" fw={500} truncate miw={0} title={p.error ?? undefined}>
            {p.form ?? (p.error ? 'The PDF link failed' : 'Not a form housing-watch can fill')}
          </Text>
          <Group gap="xs" wrap="nowrap">
            <Button size="xs" variant="default" component="a" href={p.url} target="_blank" rel="noopener noreferrer">Open Blank PDF</Button>
            {p.form && q.data.ready && (
              <Button size="xs" component="a" href={`/api/listing/application${qs({ key: k, n: p.n })}`} download
                leftSection={<IconDownload size={14} aria-hidden />}>
                Download Filled PDF
              </Button>
            )}
          </Group>
        </Group>
      ))}
      <Text size="xs" c="dimmed">
        {q.data.ready
          ? 'Filled from your applicant file, signed and dated today. The SSN is left blank for you to write in.'
          : 'To fill known forms, set HW_APPLICANT_FILE on the server.'}
      </Text>
    </Stack>
  )
}

/** One listing: facts, every unit with your income band, application PDFs, notes and history. */
export function ListingDrawer({ k, onClose }: { k: string | null; onClose: () => void }) {
  const q = useQuery({ queryKey: ['listing', k], queryFn: () => api.get<Detail>(`/api/listing${qs({ key: k })}`), enabled: Boolean(k) })
  const motion = useMotion()
  const d = q.data
  const x = d?.summary
  return (
    <Drawer opened={Boolean(k)} onClose={onClose} position="right" size="xl" title={x?.title ?? 'Listing'}
      transitionProps={motion} closeButtonProps={{ 'aria-label': 'Close listing' }}>
      {q.isLoading && <Loading rows={8} label="Loading the listing…" />}
      {q.error && <LoadError what="this listing" error={q.error} retry={() => q.refetch()} />}
      {d && x && (
        <Stack gap="xl">
          <Stack gap="sm">
            <Group gap="xs">
              <VerdictBadge v={x.verdict} />
              <StatusBadge s={x.status} />
              <Badge variant="default" c="dimmed" translate="no">{x.source} · {x.kind}</Badge>
              <Text size="sm" c="dimmed">{x.verdict === 'eligible' ? `${x.eligible_units} of ${x.units} units fit your profile` : x.reason}</Text>
            </Group>
            {x.url && (
              <Group>
                <Button size="xs" variant="default" component="a" href={x.url} target="_blank" rel="noopener noreferrer"
                  rightSection={<IconExternalLink size={14} aria-hidden />}>View on Source Site</Button>
              </Group>
            )}
          </Stack>
          <SimpleGrid cols={{ base: 2, sm: 3 }} spacing="md" verticalSpacing="sm">
            <Fact label="Address">{x.address ?? '—'}</Fact>
            <Fact label="Borough">{x.borough ?? 'Unknown'}</Fact>
            <Fact label="Deadline">{date(x.deadline)}</Fact>
            {x.commute != null && <Fact label="Commute">{commute(x.commute, x.commute_route)}</Fact>}
            <Fact label="First seen">{when(x.first_seen)}</Fact>
            <Fact label="Last changed">{when(x.last_changed)}</Fact>
            <Fact label="Last seen">{when(d.last_seen)}</Fact>
            {x.gone_since && <Fact label="Removed">{when(x.gone_since)}</Fact>}
            {x.senior_only && <Fact label="Restriction">Seniors only</Fact>}
          </SimpleGrid>

          <Section title={<>Units <Text span c="dimmed" fw={400}>({d.units.length})</Text></>}>
            {d.units.length ? (
              <Table.ScrollContainer minWidth={560}>
                <Table tabularNums fz="sm" verticalSpacing={6} aria-label="Units">
                  <Table.Thead>
                    <Table.Tr><Table.Th>Unit</Table.Th><Table.Th ta="right">Rent</Table.Th><Table.Th ta="right">AMI</Table.Th><Table.Th>Income Band</Table.Th><Table.Th>Verdict</Table.Th></Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {d.units.map((u, i) => (
                      <Table.Tr key={i} opacity={u.verdict === 'excluded' ? 0.55 : 1}>
                        <Table.Td>
                          <Group gap={4}>
                            <SizeBadge s={u.size} />{u.unit_no && <Text size="sm">#{u.unit_no}</Text>}
                            {u.set_aside && <Badge color="orange">{u.set_aside}</Badge>}
                            {u.available === false && <Badge variant="default" c="dimmed">Rented</Badge>}
                            {u.chart_year && <Badge variant={u.reason.includes('limits') ? 'light' : 'default'} color="orange" c={u.reason.includes('limits') ? undefined : 'dimmed'}>{u.chart_year} chart</Badge>}
                          </Group>
                        </Table.Td>
                        <Table.Td ta="right">{money(u.rent)}</Table.Td>
                        <Table.Td ta="right">{u.ami ? pct(u.ami) : '—'}</Table.Td>
                        <Table.Td>
                          {u.bands.length ? u.bands.map(([h, lo, hi]) => (
                            <Text key={h} size="xs" fw={h === d.household ? 600 : 400} c={h === d.household ? undefined : 'dimmed'}>
                              {h}p {lo == null ? '?' : money(lo)}–{hi == null ? '?' : money(hi)}{h === d.household ? ' (you)' : ''}
                            </Text>
                          )) : <Text size="xs" c="dimmed">No income table</Text>}
                        </Table.Td>
                        <Table.Td><Group gap={6} wrap="nowrap"><VerdictBadge v={u.verdict} />{(u.verdict !== 'eligible' || u.reason.includes('limits')) && <Text size="xs" c="dimmed">{u.reason}</Text>}</Group></Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            ) : <Text size="sm" c="dimmed">The source publishes no unit details for this listing; check the source site for rents and sizes.</Text>}
          </Section>

          <Section title="Tracker"><TrackerPanel x={x} /></Section>

          {d.pdfs.length > 0 && <Section title="Application"><ApplicationsPanel k={x.key} /></Section>}

          {d.notes && (
            <Section title="Notes">
              <Code block style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{d.notes}</Code>
            </Section>
          )}

          <Section title="History">
            {d.events.length ? (
              <Timeline>
                {d.events.map((e) => (
                  <Timeline.Item key={e.id} title={<Group gap="xs"><EventBadge t={e.type} baseline={e.baseline} /><Text size="xs" c="dimmed">{when(e.at)}{e.notified ? ' · mailed' : ''}</Text></Group>}>
                    {e.summary && <Text size="sm" c="dimmed">{e.summary}</Text>}
                  </Timeline.Item>
                ))}
              </Timeline>
            ) : <Text size="sm" c="dimmed">No events recorded for this listing yet.</Text>}
          </Section>
          <Text size="xs" c="dimmed">Key <Code translate="no">{x.key}</Code></Text>
        </Stack>
      )}
    </Drawer>
  )
}
