import { Anchor, Badge, Button, Code, Drawer, Group, Loader, SimpleGrid, Stack, Table, Text, Timeline, Title } from '@mantine/core'
import { IconDownload, IconExternalLink } from '@tabler/icons-react'
import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

import { BOROUGH, changes, KIND, request } from '../api'
import { graphql } from '../gql'
import type { ListingQuery } from '../gql/graphql'
import { commute, date, money, pct, when } from '../format'
import { useMotion } from '../hooks'
import { EventBadge, SizeBadge, StatusBadge, VerdictBadge } from './Badges'
import { Loading, LoadError } from './State'
import { TrackerPanel } from './TrackerPanel'

const ListingDoc = graphql(`
  query Listing($key: ID!) {
    listing(key: $key) {
      summary { ...ListingFields } notes ext_id last_seen household pdfs
      units { size unit_no rent ami set_aside available bands { household income_min income_max } verdict reason chart_year }
      events { ...EventFields }
    }
  }
`)

const ApplicationsDoc = graphql(`
  query ListingApplications($key: ID!) {
    listingApplications(key: $key) { ready pdfs { n url form error } }
  }
`)

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

type DrawerUnit = NonNullable<ListingQuery['listing']>['units'][number]

/** One unit: size and tags, rent, AMI, the income band for each household size (yours in bold), and its verdict. */
function UnitRow({ u, household }: { u: DrawerUnit; household: number }) {
  // the ad prints an older HUD chart, so the verdict was taken on the current one; criteria.py words that reason
  // "eligible on 2026 limits (ad prints 2025 limits: ...)", and no other unit reason says "limits"
  const rechecked = u.reason.includes('limits')
  return (
    <Table.Tr opacity={u.verdict === 'excluded' ? 0.55 : 1}>
      <Table.Td>
        <Group gap={4}>
          <SizeBadge s={u.size} />{u.unit_no && <Text size="sm">#{u.unit_no}</Text>}
          {u.set_aside && <Badge color="orange">{u.set_aside}</Badge>}
          {u.available === false && <Badge variant="default" c="dimmed">Rented</Badge>}
          {u.chart_year && <Badge variant={rechecked ? 'light' : 'default'} color="orange" c={rechecked ? undefined : 'dimmed'}>{u.chart_year} chart</Badge>}
        </Group>
      </Table.Td>
      <Table.Td ta="right">{money(u.rent)}</Table.Td>
      <Table.Td ta="right">{u.ami ? pct(u.ami) : '—'}</Table.Td>
      <Table.Td>
        {u.bands.length ? u.bands.map(({ household: h, income_min: lo, income_max: hi }) => (
          <Text key={h} size="xs" fw={h === household ? 600 : 400} c={h === household ? undefined : 'dimmed'}>
            {h}p {lo == null ? '?' : money(lo)}–{hi == null ? '?' : money(hi)}{h === household ? ' (you)' : ''}
          </Text>
        )) : <Text size="xs" c="dimmed">No income table</Text>}
      </Table.Td>
      <Table.Td><Group gap={6} wrap="nowrap"><VerdictBadge v={u.verdict} />{(u.verdict !== 'eligible' || rechecked) && <Text size="xs" c="dimmed">{u.reason}</Text>}</Group></Table.Td>
    </Table.Tr>
  )
}

function ApplicationsPanel({ k }: { k: string }) {
  const q = useQuery({ queryKey: ['applications', k], queryFn: () => request(ApplicationsDoc, { key: k }).then((d) => d.listingApplications) })
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
              <Button size="xs" component="a" href={`/api/listing/application?${new URLSearchParams({ key: k, n: String(p.n) })}`} download
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
  const q = useQuery({ queryKey: ['listing', k], queryFn: () => request(ListingDoc, { key: k! }).then((d) => d.listing), enabled: Boolean(k) })
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
              <Badge variant="default" c="dimmed" translate="no">{x.source} · {KIND[x.kind]}</Badge>
              <Text size="sm" c="dimmed">{x.verdict === 'eligible' ? `${x.eligible_units} of ${x.units} units fit your profile` : x.reason}</Text>
            </Group>
            {x.copy_of && (
              <Text size="sm" c="dimmed">An aggregator’s copy of <Anchor component={Link} to={`?${new URLSearchParams({ open: x.copy_of })}`} inherit>the real posting</Anchor>, which stands for it in lists and the digest.</Text>
            )}
            {x.copies.length > 0 && (
              <Text size="sm" c="dimmed">Also listed by {x.copies.map((k, i) => (
                <span key={k}>{i > 0 && ', '}<Anchor component={Link} to={`?${new URLSearchParams({ open: k })}`} inherit translate="no">{k.split(':')[0]}</Anchor></span>
              ))}.</Text>
            )}
            {x.url && (
              <Group>
                <Button size="xs" variant="default" component="a" href={x.url} target="_blank" rel="noopener noreferrer"
                  rightSection={<IconExternalLink size={14} aria-hidden />}>View on Source Site</Button>
              </Group>
            )}
          </Stack>
          <SimpleGrid cols={{ base: 2, sm: 3 }} spacing="md" verticalSpacing="sm">
            <Fact label="Address">{x.address ?? '—'}</Fact>
            <Fact label="Borough">{x.borough ? BOROUGH[x.borough] : 'Unknown'}</Fact>
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
                    {d.units.map((u, i) => <UnitRow key={i} u={u} household={d.household} />)}
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
                    {changes(e).length
                      ? changes(e).map((c) => <Text key={c} size="sm" c="dimmed">{c}</Text>)
                      : e.summary && <Text size="sm" c="dimmed">{e.summary}</Text>}
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
