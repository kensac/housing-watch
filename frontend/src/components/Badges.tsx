import { Badge, Group, Text, Tooltip } from '@mantine/core'

import type { StageGroup, Verdict } from '../api'
import { EVENT_COLOR, STATUS_COLOR, stageAge, VERDICT_COLOR, when } from '../format'
import { useMotion } from '../hooks'

// a badge never shrinks below its label (its ellipsis would otherwise let a table squeeze it to nothing)
const whole = { minWidth: 'max-content' }

export function VerdictBadge({ v, why }: { v: Verdict | null; why?: string }) {
  const motion = useMotion()
  if (!v) return null
  return (
    <Tooltip label={why} disabled={!why} withinPortal transitionProps={motion}>
      <Badge color={VERDICT_COLOR[v]} variant="light" style={whole}>{v}</Badge>
    </Tooltip>
  )
}

export const SizeBadge = ({ s }: { s: string }) => <Badge variant="default" size="sm" style={whole}>{s}</Badge>

export const StatusBadge = ({ s }: { s: string }) => <Badge color={STATUS_COLOR[s] ?? 'gray'} variant="outline" style={whole}>{s}</Badge>

export const EventBadge = ({ t, baseline }: { t: string; baseline?: boolean }) => (
  <Badge color={EVENT_COLOR[t] ?? 'gray'} variant={baseline ? 'outline' : 'light'} style={whole}>
    {baseline && t === 'new' ? 'first run' : t.replaceAll('_', ' ')}
  </Badge>
)

// an application stage, coloured by whose move it is: yours, theirs, or done (an offer, or closed)
const GROUP_COLOR: Record<string, string> = { 'your move': 'orange', 'their move': 'blue' }
const stageColor = (s: string, group: StageGroup | null | undefined) => (s === 'Offer' ? 'green' : GROUP_COLOR[group ?? ''] ?? 'gray')

/** The stage, and while someone still has to move, how long it has been in it. */
export function ApplicationBadge({ s, group, since, baseline }: {
  s: string | null | undefined; group?: StageGroup | null; since?: string | null; baseline?: boolean
}) {
  if (!s) return <>—</>
  const age = group === 'your move' || group === 'their move' ? stageAge(since, baseline) : null
  return (
    <Group gap={6} wrap="nowrap">
      <Badge color={stageColor(s, group)} variant="light" style={whole} title={group ?? 'not a known stage'}>{s}</Badge>
      {age && <Text size="xs" c="dimmed" style={whole} title={baseline ? `Already ${s} when tracking began, ${when(since)}` : when(since)}>{age}</Text>}
    </Group>
  )
}
