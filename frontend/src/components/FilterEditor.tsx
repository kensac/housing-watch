import { Button, Checkbox, Group, Loader, NumberInput, ScrollArea, SegmentedControl, Select, Stack, Text, TextInput } from '@mantine/core'
import { DateInput } from '@mantine/dates'
import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import { api, qs, type Chip, type Field, type Page } from '../api'
import { useMeta } from '../hooks'

const NO_VALUE = new Set(['empty', 'notempty'])
const escape = (v: string) => v.replace(/,/g, '\\,')

function filterString(field: string, op: string, values: string[]): string {
  return NO_VALUE.has(op) ? `${field}.${op}` : `${field}.${op}.${values.map(escape).join(',')}`
}

function defaultOp(f: Field, isEnum: boolean): string {
  if (f.type === 'number') return 'le'
  if (f.type === 'time') return 'last'
  if (f.type === 'bool') return 'is'
  return isEnum ? 'in' : 'contains'
}

interface Props {
  page: Page
  current?: Chip // editing this filter; otherwise adding one
  others: string[] // the other filters, for value counts
  q: string
  onApply: (f: string) => void
  onRemove?: () => void
}

/** Pick a field, then an operator and a value that fit its type (enums list their values with counts). */
export function FilterEditor({ page, current, others, q, onApply, onRemove }: Props) {
  const meta = useMeta().data!
  const fields = meta.pages[page].fields
  const [name, setName] = useState<string | null>(current?.field ?? null)
  const field = fields.find((f) => f.name === name)
  const values = useQuery({
    queryKey: ['values', page, name, others, q],
    queryFn: () => api.get<{ enum: boolean; counts: { value: string | null; count: number }[] }>(
      `/api/values${qs({ page, field: name, f: others, q })}`),
    enabled: Boolean(field && field.type === 'text'),
  })
  const isEnum = Boolean(values.data?.enum)
  const [op, setOp] = useState<string | null>(current?.op ?? null)
  const [rawVals, setVals] = useState<string[]>(current?.values ?? [])
  const vals = field?.type === 'bool' && !rawVals.length ? ['yes'] : rawVals
  const effOp = op ?? (field && (field.type !== 'text' || values.data) ? defaultOp(field, isEnum) : null)

  const groups = useMemo(() => {
    const by = new Map<string, { value: string; label: string }[]>()
    for (const f of fields) by.set(f.group, [...(by.get(f.group) ?? []), { value: f.name, label: f.label }])
    return [...by].map(([group, items]) => ({ group, items }))
  }, [fields])

  const pick = (n: string | null) => { setName(n); setOp(null); setVals([]) }
  const need = !effOp || NO_VALUE.has(effOp) ? 0 : effOp === 'between' ? 2 : effOp === 'in' || effOp === 'nin' ? -1 : 1
  const valid = Boolean(field && effOp) && (need === 0 || (need === -1 ? vals.length > 0 : vals.slice(0, need).every((v) => v !== '' && v != null) && vals.length >= need))

  return (
    <Stack gap="sm" w={320}>
      <Select label="Field" placeholder="Pick a field" data={groups} value={name} onChange={pick} searchable
        comboboxProps={{ withinPortal: false }} />
      {field && field.type === 'text' && values.isLoading && <Loader size="sm" />}
      {field && effOp && (
        <>
          <Select label="Condition" data={field.ops.filter((o) => isEnum || field.type !== 'text' || !['in', 'nin'].includes(o) || field.choices.length)
            .map((o) => ({ value: o, label: meta.op_labels[o] ?? o }))} value={effOp}
            onChange={(o) => { setOp(o); setVals([]) }} allowDeselect={false} comboboxProps={{ withinPortal: false }} />
          <ValueInput field={field} op={effOp} vals={vals} setVals={setVals} counts={values.data?.counts} />
        </>
      )}
      <Group justify="space-between">
        {onRemove ? <Button variant="subtle" color="red" onClick={onRemove}>Remove</Button> : <span />}
        <Button disabled={!valid} onClick={() => onApply(filterString(field!.name, effOp!, need === 0 ? [] : vals))}>
          {current ? 'Update' : 'Add filter'}
        </Button>
      </Group>
    </Stack>
  )
}

function ValueInput({ field, op, vals, setVals, counts }: {
  field: Field; op: string; vals: string[]; setVals: (v: string[]) => void
  counts?: { value: string | null; count: number }[]
}) {
  const at = (i: number) => vals[i] ?? ''
  const put = (i: number, v: string) => { const n = [...vals]; n[i] = v; setVals(n) }
  if (NO_VALUE.has(op)) return null
  if (field.type === 'bool') {
    return <SegmentedControl data={[{ value: 'yes', label: 'Yes' }, { value: 'no', label: 'No' }]}
      value={at(0)} onChange={(v) => setVals([v])} />
  }
  if (field.type === 'text' && (op === 'in' || op === 'nin')) {
    const opts = counts ?? field.choices.map((c) => ({ value: c, count: 0 }))
    return (
      <ScrollArea.Autosize mah={260}>
        <Checkbox.Group value={vals} onChange={setVals}>
          <Stack gap={6}>
            {opts.map((o) => (
              <Checkbox key={o.value ?? ''} value={o.value ?? ''} label={
                <Group gap={6} wrap="nowrap"><Text size="sm">{o.value || '(empty)'}</Text>
                  {counts && <Text size="xs" c="dimmed">{o.count.toLocaleString()}</Text>}</Group>} />
            ))}
          </Stack>
        </Checkbox.Group>
      </ScrollArea.Autosize>
    )
  }
  if (field.type === 'text') return <TextInput label="Text" value={at(0)} onChange={(e) => setVals([e.currentTarget.value])} data-autofocus />
  if (field.type === 'number') {
    const num = (i: number, label: string) => (
      <NumberInput key={i} label={label} value={at(i)} onChange={(v) => put(i, String(v))} thousandSeparator=","
        prefix={field.money ? '$' : undefined} />
    )
    return op === 'between' ? <Group grow>{num(0, 'From')}{num(1, 'To')}</Group> : num(0, 'Value')
  }
  if (op === 'last') {
    const m = /^(\d*)([mhd]?)$/.exec(at(0)) ?? ['', '', 'd']
    const n = m[1] ?? '', unit = m[2] || 'd'
    return (
      <Group grow>
        <NumberInput label="Amount" min={1} value={n} onChange={(v) => setVals([`${v}${unit}`])} />
        <Select label="Unit" data={[{ value: 'h', label: 'hours' }, { value: 'd', label: 'days' }, { value: 'm', label: 'minutes' }]}
          value={unit} onChange={(u) => setVals([`${n}${u ?? 'd'}`])} allowDeselect={false} comboboxProps={{ withinPortal: false }} />
      </Group>
    )
  }
  const date = (i: number, label: string) => (
    <DateInput key={i} label={label} value={at(i) || null} onChange={(v) => put(i, v ?? '')} valueFormat="MMM D, YYYY"
      popoverProps={{ withinPortal: false }} />
  )
  return op === 'between' ? <Group grow>{date(0, 'From')}{date(1, 'To')}</Group> : date(0, 'Date')
}
