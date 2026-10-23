import { Button, Checkbox, Group, Loader, NumberInput, ScrollArea, SegmentedControl, Select, Stack, Text, TextInput } from '@mantine/core'
import { DateInput, DateTimePicker } from '@mantine/dates'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'
import { useMemo, useState } from 'react'

import { request, type Field, type FilterOp, type Page } from '../api'
import { pageFilter, type Json } from '../filters'
import { graphql } from '../gql'
import { num } from '../format'
import { useMeta } from '../hooks'

const ValuesDoc = graphql(`
  query Values($field: FilterFieldName!, $filter: PageFilterInput!, $q: String! = "") {
    values(field: $field, filter: $filter, q: $q) { enum counts { value label count } }
  }
`)

/** One condition: operator `op` of `field`, holding `value` (the filter object's JSON for that operator). */
export interface Condition { field: Field; op: FilterOp; value: Json }

// the editor offers `empty` as two choices: empty: true and empty: false
type UiOp = FilterOp | 'not_empty'
const UNIT_LABEL = { minutes: 'minutes', hours: 'hours', days: 'days' }
const isUnit = (u: string): u is keyof typeof UNIT_LABEL => u in UNIT_LABEL
const isRecord = (x: unknown): x is Record<string, unknown> => typeof x === 'object' && x !== null && !Array.isArray(x)
const text = (v: unknown) => (typeof v === 'string' || typeof v === 'number' ? String(v) : '')

function defaultOp(f: Field, isEnum: boolean): UiOp {
  if (f.type === 'number') return 'le'
  if (f.type === 'time') return 'last'
  if (f.type === 'bool') return 'is'
  return f.type === 'enum' || isEnum ? 'in' : 'contains'
}

/** The editor's inputs for a condition's value. */
function toVals(op: UiOp, value: unknown): string[] {
  if (Array.isArray(value)) return value.filter((x) => typeof x === 'string')
  if (typeof value === 'boolean') return [value ? 'yes' : 'no']
  if (op === 'since' || op === 'until') return typeof value === 'string' ? [dayjs(value).format('YYYY-MM-DD HH:mm:ss')] : []
  if (isRecord(value)) {
    if ('min' in value) return [text(value.min), text(value.max)]
    if ('from' in value) return [text(value.from), text(value.to)]
    if ('amount' in value) return [text(value.amount), text(value.unit)]
  }
  return value === undefined || value === null ? [] : [text(value)]
}

/** The condition the inputs make, or null while one is missing. */
function toCondition(field: Field, op: UiOp, vals: string[]): Condition | null {
  const at = (i: number) => vals[i] ?? ''
  const n = (i: number) => (at(i).trim() === '' ? NaN : Number(at(i)))
  if (op === 'empty' || op === 'not_empty') return { field, op: 'empty', value: op === 'empty' }
  if (op === 'in' || op === 'not_in') return vals.length ? { field, op, value: vals } : null
  if (op === 'contains' || op === 'not_contains') return at(0).trim() ? { field, op, value: at(0) } : null
  if (op === 'is') return { field, op, value: at(0) !== 'no' }
  if (op === 'last') {
    const unit = at(1) || 'days'
    return Number.isInteger(n(0)) && n(0) >= 1 && isUnit(unit) ? { field, op, value: { amount: n(0), unit } } : null
  }
  if (op === 'since' || op === 'until') return at(0) ? { field, op, value: dayjs(at(0)).format() } : null
  if (op === 'after' || op === 'before') return at(0) ? { field, op, value: at(0) } : null
  if (op === 'between' && field.type === 'time') return at(0) && at(1) ? { field, op, value: { from: at(0), to: at(1) } } : null
  if (op === 'between') return Number.isFinite(n(0)) && Number.isFinite(n(1)) ? { field, op, value: { min: n(0), max: n(1) } } : null
  return Number.isFinite(n(0)) ? { field, op, value: n(0) } : null
}

const MISSING: Partial<Record<UiOp, string>> = {
  in: 'Check at least one value.', not_in: 'Check at least one value.', between: 'Enter both ends of the range.',
  last: 'Enter a whole number of 1 or more.',
}

interface Props {
  page: Page
  current?: { field: Field; op: FilterOp; value: unknown } // editing this condition; otherwise adding one
  others: unknown // the filter without it, for value counts
  q: string
  onApply: (c: Condition) => void
  onRemove?: () => void
}

/** Pick a field, then an operator and a value that fit its type (enums list their values with counts). */
export function FilterEditor({ page, current, others, q, onApply, onRemove }: Props) {
  const meta = useMeta().data!
  const fields = meta.pages[page].fields
  const opLabel = useMemo(() => {
    const m = new Map<UiOp, string>(meta.ops.map((o) => [o.op, o.label]))
    return m.set('not_empty', 'is not empty')
  }, [meta.ops])
  const startOp: UiOp | null = current ? (current.op === 'empty' && current.value === false ? 'not_empty' : current.op) : null
  const [name, setName] = useState<string | null>(current?.field.name ?? null)
  const field = fields.find((f) => f.name === name)
  const values = useQuery({
    queryKey: ['values', page, name, others, q],
    queryFn: () => (field
      ? request(ValuesDoc, { field: field.name, filter: pageFilter(page, others, meta), q }).then((d) => d.values)
      : Promise.reject(new Error('Pick a field first'))),
    enabled: Boolean(field && (field.type === 'text' || field.type === 'enum')),
  })
  const isEnum = Boolean(values.data?.enum) || field?.type === 'enum'
  const [op, setOp] = useState<UiOp | null>(startOp)
  const [vals, setVals] = useState<string[]>(current ? toVals(startOp ?? current.op, current.value) : [])
  const [tried, setTried] = useState(false) // show what is missing only after an attempt to apply
  const effOp = op ?? (field && (field.type !== 'text' || values.data) ? defaultOp(field, isEnum) : null)
  const cond = field && effOp ? toCondition(field, effOp, vals) : null

  const groups = useMemo(() => {
    const by = new Map<string, { value: string; label: string }[]>()
    for (const f of fields) by.set(f.group, [...(by.get(f.group) ?? []), { value: f.name, label: f.label }])
    return [...by].map(([group, items]) => ({ group, items }))
  }, [fields])

  const ops = (field?.ops ?? []).flatMap((o): UiOp[] => (o === 'empty' ? ['empty', 'not_empty'] : [o]))
    .filter((o) => isEnum || field?.type !== 'text' || (o !== 'in' && o !== 'not_in'))
  const pick = (n: string | null) => { setName(n); setOp(null); setVals([]); setTried(false) }
  const fieldError = tried && !field ? 'Pick a field to filter on.' : null
  const valueError = tried && field && effOp && !cond ? MISSING[effOp] ?? 'Enter a value.' : null

  return (
    <form noValidate onSubmit={(e) => {
      e.preventDefault()
      setTried(true)
      if (cond) onApply(cond)
    }}>
      <Stack gap="sm" w={{ base: 'calc(100vw - 64px)', xs: 320 }} maw={320}>
        <Select label="Field" placeholder="Pick a field…" data={groups} value={name} onChange={pick} searchable
          error={fieldError} comboboxProps={{ withinPortal: false }} data-autofocus={current ? undefined : true} />
        {field && (field.type === 'text' || field.type === 'enum') && values.isLoading && (
          <Group gap="xs" role="status"><Loader size="xs" /><Text size="sm" c="dimmed">Loading values…</Text></Group>
        )}
        {field && field.type === 'text' && values.error && (
          <Text size="sm" c="red" role="alert">Couldn’t load this field’s values; you can still filter on text.</Text>
        )}
        {field && effOp && (
          <>
            <Select label="Condition" data={ops.map((o) => ({ value: o, label: opLabel.get(o) ?? o }))} value={effOp}
              onChange={(o) => { const next = ops.find((x) => x === o); if (next) { setOp(next); setVals([]) } }}
              allowDeselect={false} comboboxProps={{ withinPortal: false }} />
            <ValueInput field={field} op={effOp} vals={vals} setVals={setVals} counts={values.data?.counts} error={valueError} />
          </>
        )}
        <Group justify="space-between">
          {onRemove ? <Button variant="subtle" color="red" onClick={onRemove}>Remove Filter</Button> : <span />}
          <Button type="submit">{current ? 'Update Filter' : 'Add Filter'}</Button>
        </Group>
      </Stack>
    </form>
  )
}

function ValueInput({ field, op, vals, setVals, counts, error }: {
  field: Field; op: UiOp; vals: string[]; setVals: (v: string[]) => void
  counts?: { value: string | null; label: string; count: number }[]; error: string | null
}) {
  const at = (i: number) => vals[i] ?? ''
  const put = (i: number, v: string) => { const n = [...vals]; n[i] = v; setVals(n) }
  if (op === 'empty' || op === 'not_empty') return null
  if (field.type === 'bool') {
    return <SegmentedControl aria-label={field.label} data={[{ value: 'yes', label: 'Yes' }, { value: 'no', label: 'No' }]}
      value={at(0) || 'yes'} onChange={(v) => setVals([v])} />
  }
  if (op === 'in' || op === 'not_in') {
    // an empty value can't be picked here: "is empty" asks for it
    const opts = counts?.flatMap((c) => (c.value === null ? [] : [{ value: c.value, label: c.label, count: c.count }]))
      ?? field.choices.map((c) => ({ value: c.name, label: c.label, count: 0 }))
    return (
      <Checkbox.Group label="Values" value={vals} onChange={setVals} error={error}>
        <ScrollArea.Autosize mah={260} mt={6}>
          <Stack gap={6}>
            {opts.length === 0 && <Text size="sm" c="dimmed">No values under the other filters.</Text>}
            {opts.map((o) => (
              <Checkbox key={o.value} value={o.value} label={
                <Group gap={6} wrap="nowrap"><Text size="sm">{o.label}</Text>
                  {counts && <Text size="xs" c="dimmed">{num(o.count)}</Text>}</Group>} />
            ))}
          </Stack>
        </ScrollArea.Autosize>
      </Checkbox.Group>
    )
  }
  if (field.type === 'text' || field.type === 'enum') {
    return <TextInput label="Text" name="filter-text" autoComplete="off" placeholder="Text to match…" value={at(0)} error={error}
      onChange={(e) => setVals([e.currentTarget.value])} data-autofocus />
  }
  if (field.type === 'number') {
    const box = (i: number, label: string) => (
      <NumberInput key={i} label={label} name={`filter-${field.name}-${i}`} autoComplete="off" value={at(i)} onChange={(v) => put(i, String(v))}
        thousandSeparator="," prefix={field.money ? '$' : undefined} error={i === 0 ? error : undefined} />
    )
    return op === 'between' ? <Group grow align="flex-start">{box(0, 'From')}{box(1, 'To')}</Group> : box(0, 'Value')
  }
  if (op === 'last') {
    const unit = at(1) || 'days'
    return (
      <Group grow align="flex-start">
        <NumberInput label="Amount" name="filter-amount" autoComplete="off" min={1} allowDecimal={false} value={at(0)} error={error}
          onChange={(v) => setVals([String(v), unit])} />
        <Select label="Unit" data={Object.entries(UNIT_LABEL).map(([value, label]) => ({ value, label }))}
          value={unit} onChange={(u) => setVals([at(0), u ?? 'days'])} allowDeselect={false} comboboxProps={{ withinPortal: false }} />
      </Group>
    )
  }
  if (op === 'since' || op === 'until') {
    return <DateTimePicker label="Date and time" value={at(0) || null} onChange={(v) => setVals(v ? [v] : [])} valueFormat="MMM D, YYYY h:mm A"
      placeholder="Pick a date and time…" error={error} popoverProps={{ withinPortal: false }} />
  }
  const date = (i: number, label: string) => (
    <DateInput key={i} label={label} value={at(i) || null} onChange={(v) => put(i, v ?? '')} valueFormat="MMM D, YYYY"
      placeholder="Pick a date…" error={i === 0 ? error : undefined} popoverProps={{ withinPortal: false }} />
  )
  return op === 'between' ? <Group grow align="flex-start">{date(0, 'From')}{date(1, 'To')}</Group> : date(0, 'Date')
}
