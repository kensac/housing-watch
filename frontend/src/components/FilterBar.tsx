import { Button, Pill, Popover, UnstyledButton } from '@mantine/core'
import { IconFilterPlus } from '@tabler/icons-react'
import { useState } from 'react'

import type { Chip, Page } from '../api'
import { getCond, setCond } from '../filters'
import { useMeta, useMotion } from '../hooks'
import classes from './FilterBar.module.css'
import { FilterEditor, type Condition } from './FilterEditor'

interface Props {
  page: Page
  filter: unknown // the page's filter object
  chips: Chip[] // its conditions in words, from the server: one per field and operator
  q: string
  onChange: (filter: unknown) => void
}

/** The filter's conditions as chips (click one to edit it, × to remove it) plus "Add Filter", laid out inline in the
 * toolbar; every change is in the URL, so the browser's Back undoes it. */
export function FilterBar({ page, filter, chips, q, onChange }: Props) {
  const fields = useMeta().data?.pages[page].fields ?? []
  const [editing, setEditing] = useState<string | null>(null) // a chip's key, or 'new'
  const motion = useMotion()
  const close = () => setEditing(null)
  const conds = chips.flatMap((c) => {
    const field = fields.find((f) => f.name === c.field)
    return field ? [{ chip: c, cond: { field, op: c.op, value: getCond(filter, field.path, c.op) }, key: `${c.field}.${c.op}` }] : []
  })
  const without = (c: { field: { path: readonly string[] }; op: string }) => setCond(filter, c.field.path, c.op, undefined)
  const put = (base: unknown, c: Condition) => setCond(base, c.field.path, c.op, c.value)
  return (
    <>
      <Popover opened={editing === 'new'} onChange={(o) => !o && close()} position="bottom-start" trapFocus returnFocus transitionProps={motion}>
        <Popover.Target>
          <Button variant="default" size="xs" leftSection={<IconFilterPlus size={15} aria-hidden />}
            onClick={() => setEditing(editing === 'new' ? null : 'new')} aria-haspopup="dialog" aria-expanded={editing === 'new'}>
            Add Filter
          </Button>
        </Popover.Target>
        <Popover.Dropdown aria-label="Add a filter">
          {editing === 'new' && (
            <FilterEditor page={page} others={filter} q={q} onApply={(c) => { onChange(put(filter, c)); close() }} />
          )}
        </Popover.Dropdown>
      </Popover>
      {conds.map(({ chip, cond, key }) => (
        <Popover key={key} opened={editing === key} onChange={(o) => !o && close()} position="bottom-start" trapFocus returnFocus transitionProps={motion}>
          <Popover.Target>
            <Pill withRemoveButton onRemove={() => onChange(without(cond))} classNames={{ root: classes.chip, remove: classes.remove }}
              removeButtonProps={{ 'aria-hidden': false, tabIndex: 0, 'aria-label': `Remove filter: ${chip.label}` }}>
              <UnstyledButton className={classes.edit} onClick={() => setEditing(editing === key ? null : key)}
                aria-haspopup="dialog" aria-expanded={editing === key} aria-label={`Edit filter: ${chip.label}`}>
                {chip.label}
              </UnstyledButton>
            </Pill>
          </Popover.Target>
          <Popover.Dropdown aria-label={`Edit filter: ${chip.label}`}>
            {editing === key && (
              <FilterEditor page={page} current={cond} others={without(cond)} q={q}
                onApply={(c) => { onChange(put(without(cond), c)); close() }}
                onRemove={() => { onChange(without(cond)); close() }} />
            )}
          </Popover.Dropdown>
        </Popover>
      ))}
      {chips.length > 0 && <Button variant="subtle" size="xs" c="dimmed" onClick={() => onChange({})}>Clear Filters</Button>}
    </>
  )
}
