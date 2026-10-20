import { Badge, Tooltip } from '@mantine/core'

import type { Verdict } from '../api'
import { EVENT_COLOR, STATUS_COLOR, VERDICT_COLOR } from '../format'
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

// the tracker sheet's Status dropdown, grouped by what it means for "what to apply to next"
const APPLICATION_COLOR: Record<string, string> = {
  'To Apply': 'orange', Seen: 'gray', Applied: 'blue', 'Interest Form Filled': 'blue', 'Under Review': 'indigo',
  'Appeal Under Review': 'indigo', Contacted: 'teal', Rejected: 'red', 'Not interested': 'gray', Closed: 'gray',
}

export const ApplicationBadge = ({ s }: { s: string | null | undefined }) => (s
  ? <Badge color={APPLICATION_COLOR[s] ?? 'gray'} variant="light" style={whole}>{s}</Badge>
  : <>—</>)
