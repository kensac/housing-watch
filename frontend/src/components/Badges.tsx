import { Badge, Tooltip } from '@mantine/core'

// a badge never shrinks below its label (its ellipsis would otherwise let a table squeeze it to nothing)
const whole = { minWidth: 'max-content' }

import type { Verdict } from '../api'
import { EVENT_COLOR, STATUS_COLOR, VERDICT_COLOR } from '../format'

export const VerdictBadge = ({ v, why }: { v: Verdict | null; why?: string }) => (v ? (
  <Tooltip label={why} disabled={!why} withinPortal>
    <Badge color={VERDICT_COLOR[v]} variant="light" style={whole}>{v}</Badge>
  </Tooltip>
) : null)

export const SizeBadge = ({ s }: { s: string }) => <Badge variant="default" size="sm" style={whole}>{s}</Badge>

export const StatusBadge = ({ s }: { s: string }) => <Badge color={STATUS_COLOR[s] ?? 'gray'} variant="outline" style={whole}>{s}</Badge>

export const EventBadge = ({ t, baseline }: { t: string; baseline?: boolean }) => (
  <Badge color={EVENT_COLOR[t] ?? 'gray'} variant={baseline ? 'outline' : 'light'} style={whole}>{baseline && t === 'new' ? 'first run' : t.replace('_', ' ')}</Badge>
)
