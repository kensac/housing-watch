import { Badge, Group, Text, Tooltip } from '@mantine/core'

import { SIZE, STAGE_GROUP, type EventType, type Size, type StageGroup, type Status, type Verdict } from '../api'
import { EVENT_COLOR, STATUS_COLOR, stageAge, VERDICT_COLOR, when } from '../format'
import { useMotion } from '../hooks'
import classes from './Badges.module.css'

// a badge never shrinks below its label (its ellipsis would otherwise let a table squeeze it to nothing)
const whole = { minWidth: 'max-content' }

// enum values arrive lowercase ("eligible", "page_changed"); shown in sentence case
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

export function VerdictBadge({ v, why }: { v: Verdict | null; why?: string }) {
  const motion = useMotion()
  if (!v) return null
  return (
    <Tooltip label={why} disabled={!why} withinPortal transitionProps={motion}>
      <Badge color={VERDICT_COLOR[v]} style={whole}>{cap(v)}</Badge>
    </Tooltip>
  )
}

/** A unit size: "Studio", "1BR". */
export const SizeBadge = ({ s }: { s: Size }) => (
  <Badge variant="default" c="dimmed" style={whole}>{SIZE[s]}</Badge>
)

/** A coloured dot: a status that reads at a glance without a filled chip. */
export const Dot = ({ color }: { color: string }) => (
  <span className={classes.dot} style={{ '--dot': `var(--mantine-color-${color}-6)` } as React.CSSProperties} aria-hidden />
)

export const StatusBadge = ({ s }: { s: Status }) => (
  <span className={classes.status} style={whole}><Dot color={STATUS_COLOR[s]} />{cap(s)}</span>
)

export const EventBadge = ({ t, baseline }: { t: EventType; baseline?: boolean }) => (
  <Badge color={EVENT_COLOR[t]} variant={baseline ? 'default' : 'light'} c={baseline ? 'dimmed' : undefined} style={whole}>
    {baseline && t === 'new' ? 'First run' : cap(t.replaceAll('_', ' '))}
  </Badge>
)

// an application stage, coloured by whose move it is: yours, theirs, or done (an offer, or closed)
const GROUP_COLOR: Record<StageGroup, string> = { your_move: 'orange', their_move: 'blue', done: 'gray' }
const stageColor = (s: string, group: StageGroup | null | undefined) => (s === 'Offer' ? 'green' : group ? GROUP_COLOR[group] : 'gray')

/** The stage, and while someone still has to move, how long it has been in it. */
export function ApplicationBadge({ s, group, since, baseline }: {
  s: string | null | undefined; group?: StageGroup | null; since?: string | null; baseline?: boolean
}) {
  if (!s) return <>—</>
  const age = group === 'your_move' || group === 'their_move' ? stageAge(since, baseline) : null
  return (
    <Group gap={6} wrap="nowrap">
      <Badge color={stageColor(s, group)} style={whole} title={group ? STAGE_GROUP[group] : 'not a known stage'}>{s}</Badge>
      {age && <Text size="xs" c="dimmed" style={whole} title={baseline ? `Already ${s} when tracking began, ${when(since)}` : when(since)}>{age}</Text>}
    </Group>
  )
}
