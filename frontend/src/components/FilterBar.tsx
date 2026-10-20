import { Alert, Button, Group, Pill, Popover, UnstyledButton } from '@mantine/core'
import { IconFilterPlus } from '@tabler/icons-react'
import { useState } from 'react'

import type { Chip, Page } from '../api'
import { useMotion } from '../hooks'
import { FilterEditor } from './FilterEditor'

interface Props {
  page: Page
  f: string[]
  chips: Chip[] // labels from the server; chip.i is its index in `f` (filters that did not parse have none)
  problems: string[]
  q: string
  onChange: (f: string[]) => void
}

/** The active filters as pills (click one to edit it, × to remove it) plus "Add Filter"; every change is in the
 * URL, so the browser's Back undoes it. */
export function FilterBar({ page, f, chips, problems, q, onChange }: Props) {
  const [editing, setEditing] = useState<number | 'new' | null>(null)
  const motion = useMotion()
  const others = (i: number | 'new') => f.filter((_, j) => j !== i)
  const close = () => setEditing(null)
  return (
    <>
      <Group gap="xs" role="group" aria-label="Filters">
        {chips.map((c) => { const i = c.i; return (
          <Popover key={c.f} opened={editing === i} onChange={(o) => !o && close()} position="bottom-start" withArrow trapFocus
            returnFocus transitionProps={motion}>
            <Popover.Target>
              <Pill size="lg" withRemoveButton onRemove={() => onChange(others(i))}
                removeButtonProps={{ 'aria-hidden': false, tabIndex: 0, 'aria-label': `Remove filter: ${c.label}` }}>
                <UnstyledButton fz="inherit" lh="inherit" onClick={() => setEditing(editing === i ? null : i)}
                  aria-haspopup="dialog" aria-expanded={editing === i} aria-label={`Edit filter: ${c.label}`}>
                  {c.label}
                </UnstyledButton>
              </Pill>
            </Popover.Target>
            <Popover.Dropdown aria-label={`Edit filter: ${c.label}`}>
              {editing === i && (
                <FilterEditor page={page} current={c} others={others(i)} q={q}
                  onApply={(nf) => { onChange(f.map((x, j) => (j === i ? nf : x))); close() }}
                  onRemove={() => { onChange(others(i)); close() }} />
              )}
            </Popover.Dropdown>
          </Popover>
        ) })}
        <Popover opened={editing === 'new'} onChange={(o) => !o && close()} position="bottom-start" withArrow trapFocus
          returnFocus transitionProps={motion}>
          <Popover.Target>
            <Button variant="default" size="xs" leftSection={<IconFilterPlus size={14} aria-hidden />}
              onClick={() => setEditing(editing === 'new' ? null : 'new')} aria-haspopup="dialog" aria-expanded={editing === 'new'}>
              Add Filter
            </Button>
          </Popover.Target>
          <Popover.Dropdown aria-label="Add a filter">
            {editing === 'new' && (
              <FilterEditor page={page} others={f} q={q} onApply={(nf) => { onChange([...f, nf]); close() }} />
            )}
          </Popover.Dropdown>
        </Popover>
        {f.length > 0 && <Button variant="subtle" size="xs" onClick={() => onChange([])}>Clear Filters</Button>}
      </Group>
      {problems.map((p) => (
        <Alert key={p} color="red" variant="light" title="Filter ignored: it has no effect on the list">{p}</Alert>
      ))}
    </>
  )
}
