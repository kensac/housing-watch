import { Alert, Button, Group, Pill, Popover } from '@mantine/core'
import { IconFilterPlus } from '@tabler/icons-react'
import { useState } from 'react'

import type { Chip, Page } from '../api'
import { FilterEditor } from './FilterEditor'

interface Props {
  page: Page
  f: string[]
  chips: Chip[] // labels from the server; chip.i is its index in `f` (filters that did not parse have none)
  problems: string[]
  q: string
  onChange: (f: string[]) => void
}

/** The active filters as pills (click one to edit it) plus "Add filter"; every change is in the URL. */
export function FilterBar({ page, f, chips, problems, q, onChange }: Props) {
  const [editing, setEditing] = useState<number | 'new' | null>(null)
  const others = (i: number | 'new') => f.filter((_, j) => j !== i)
  const close = () => setEditing(null)
  return (
    <>
      <Group gap="xs">
        {chips.map((c) => { const i = c.i; return (
          <Popover key={c.f} opened={editing === i} onChange={(o) => !o && close()} position="bottom-start" withArrow trapFocus>
            <Popover.Target>
              <Pill size="lg" withRemoveButton onRemove={() => onChange(others(i))} onClick={() => setEditing(i)}
                style={{ cursor: 'pointer' }}>{c.label}</Pill>
            </Popover.Target>
            <Popover.Dropdown>
              {editing === i && (
                <FilterEditor page={page} current={c} others={others(i)} q={q}
                  onApply={(nf) => { onChange(f.map((x, j) => (j === i ? nf : x))); close() }}
                  onRemove={() => { onChange(others(i)); close() }} />
              )}
            </Popover.Dropdown>
          </Popover>
        ) })}
        <Popover opened={editing === 'new'} onChange={(o) => !o && close()} position="bottom-start" withArrow trapFocus>
          <Popover.Target>
            <Button variant="default" size="xs" leftSection={<IconFilterPlus size={14} />} onClick={() => setEditing('new')}>
              Add filter
            </Button>
          </Popover.Target>
          <Popover.Dropdown>
            {editing === 'new' && (
              <FilterEditor page={page} others={f} q={q} onApply={(nf) => { onChange([...f, nf]); close() }} />
            )}
          </Popover.Dropdown>
        </Popover>
        {f.length > 0 && <Button variant="subtle" size="xs" onClick={() => onChange([])}>Clear</Button>}
      </Group>
      {problems.map((p) => <Alert key={p} color="red" variant="light" title="Filter ignored">{p}</Alert>)}
    </>
  )
}
