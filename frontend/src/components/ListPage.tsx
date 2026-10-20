import { Button, Center, Group, Loader, Stack, Table, Text, UnstyledButton } from '@mantine/core'
import { useIntersection } from '@mantine/hooks'
import { IconChevronDown, IconChevronUp, IconSelector } from '@tabler/icons-react'
import { memo, useEffect, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'

import type { Chip, Page, ViewState } from '../api'
import { num } from '../format'
import { descending, openHref, rowClick, stateUrl, TITLES, useListState, useMeta, useTitle, useViews } from '../hooks'
import { FilterBar } from './FilterBar'
import { Loading, LoadError } from './State'
import { Toolbar } from './Toolbar'
import { ViewBar } from './ViewBar'

export type ListCtx = ReturnType<typeof useListState>

// From sm up, a list page fills the viewport and its table scrolls inside it, so the header row stays in view;
// on a phone the page scrolls as a whole.
const FILL = { base: 'auto', sm: 'calc(100dvh - var(--app-shell-header-offset, 0rem) - 2 * var(--app-shell-padding))' }

/** A list page: its saved view, toolbar and filters, then whatever table `children` renders for the state. */
export function ListPage({ page, chips, problems, summary, children, search, error, retry }: {
  page: Page; chips: Chip[]; problems: string[]; summary: ReactNode; search?: boolean
  error?: unknown; retry?: () => void
  children: (ctx: ListCtx) => ReactNode
}) {
  const ctx = useListState(page)
  const meta = useMeta()
  const views = useViews()
  const navigate = useNavigate()
  useTitle(ctx.view?.name ?? TITLES[page])
  // a bare page URL opens the page's default view, if the user made one
  const dflt = views.data?.find((v) => v.page === page && v.is_default)
  useEffect(() => {
    if (ctx.bare && dflt) navigate(stateUrl(page, dflt.state, meta.data, dflt.id), { replace: true })
  }, [ctx.bare, dflt, meta.data, navigate, page])
  const setupError = meta.error ?? views.error
  if (setupError) return <LoadError what="the page setup" error={setupError} retry={() => { meta.refetch(); views.refetch() }} />
  if (!ctx.ready) return <Loading rows={10} label={`Loading ${TITLES[page].toLowerCase()}…`} />
  return (
    <Stack gap="sm" h={FILL}>
      <ViewBar page={page} state={ctx.state} view={ctx.view} modified={ctx.modified} />
      <Toolbar page={page} state={ctx.state} set={ctx.set} search={search} />
      <FilterBar page={page} f={ctx.state.f} chips={chips} problems={problems} q={ctx.state.q} onChange={(f) => ctx.set({ f })} />
      <Text size="sm" c="dimmed" aria-live="polite">{summary}</Text>
      {error ? <LoadError what={TITLES[page].toLowerCase()} error={error} retry={retry} /> : children(ctx)}
    </Stack>
  )
}

/** "1,234 listings · 12 eligible · 30 possible · 1,192 excluded": verdict counts ignore a verdict filter. */
export function Summary({ total, noun, vc, hiddenGone }: { total: number; noun: string; vc?: Record<string, number>; hiddenGone: boolean }) {
  return (
    <>
      {num(total)} {noun}
      {vc && <span title="Counts by verdict ignore a verdict filter"> · across verdicts: {num(vc.eligible)} eligible · {num(vc.possible)} possible · {num(vc.excluded)} excluded</span>}
      {hiddenGone && ' · removed listings hidden'}
    </>
  )
}

export type Cells<T> = Record<string, { label: string; sort?: string; right?: boolean; cell: (x: T) => ReactNode }>

interface RowsProps<T> {
  page: 'listings' | 'units' | 'events'
  rows: T[]
  cols: string // the shown column keys, comma-joined so the memo compares a string
  base: string // the list's query without `open` (useListState().base)
  cells: Cells<T>
  rowKey: (x: T) => string | number
  openKey: (x: T) => string | null // the listing a row opens, if any
  lead: (x: T, to: string | null) => ReactNode // the leading cell(s), given the row's link
  dim?: (x: T) => boolean
}

/** A list's rows. Memoized on the list itself, so opening or closing the drawer (a URL change) does not
 * re-render thousands of loaded rows. */
export const ListRows = memo(function ListRows<T>({ page, rows, cols, base, cells, rowKey, openKey, lead, dim }: RowsProps<T>) {
  const navigate = useNavigate()
  const shown = cols.split(',').filter((c) => c in cells)
  return rows.map((x) => {
    const k = openKey(x)
    const to = k ? openHref(page, base, k) : null
    return (
      <Table.Tr key={rowKey(x)} onClick={to ? rowClick(() => navigate(to)) : undefined} style={{ cursor: to ? 'pointer' : undefined }}
        opacity={dim?.(x) ? 0.6 : 1}>
        {lead(x, to)}
        {shown.map((c) => <Table.Td key={c} ta={cells[c].right ? 'right' : undefined} style={{ whiteSpace: 'nowrap' }}>{cells[c].cell(x)}</Table.Td>)}
      </Table.Tr>
    )
  })
}) as <T>(p: RowsProps<T>) => ReactNode

/** The dense, sticky-header table every list page uses; dimmed while a changed query is loading. */
export function ListTable({ label, minWidth, stale, children }: { label: string; minWidth: number; stale: boolean; children: ReactNode }) {
  return (
    <Table.ScrollContainer type="native" minWidth={minWidth} flex={1} mih={0} style={{ scrollPaddingTop: 40 }}>
      <Table stickyHeader highlightOnHover tabularNums verticalSpacing={6} aria-label={label} aria-busy={stale}
        opacity={stale ? 0.6 : 1}>
        {children}
      </Table>
    </Table.ScrollContainer>
  )
}

/** A column header that sorts by `sort` (click again to reverse). */
export function SortTh({ label, sort, state, set, ta }: {
  label: string; sort?: string; state: ViewState; set: (p: Partial<ViewState>) => void; ta?: 'right'
}) {
  if (!sort) return <Table.Th ta={ta}>{label}</Table.Th>
  const on = state.sort.replace(/^-/, '') === sort
  const desc = descending(state.sort)
  const Icon = !on ? IconSelector : desc ? IconChevronDown : IconChevronUp
  return (
    <Table.Th ta={ta} aria-sort={on ? (desc ? 'descending' : 'ascending') : 'none'}>
      <UnstyledButton onClick={() => set({ sort: on && !state.sort.startsWith('-') ? `-${sort}` : sort })}>
        <Group gap={4} wrap="nowrap" justify={ta === 'right' ? 'flex-end' : undefined}>
          <Text fw={on ? 700 : 500} size="sm">{label}</Text><Icon size={14} aria-hidden />
        </Group>
      </UnstyledButton>
    </Table.Th>
  )
}

/** The last row of an infinitely scrolling table: loads the next batch when it scrolls into view. */
export function MoreRow({ cols, more, loading, failed, onMore, total, noun, onClear, clearLabel = 'Clear Filters and Search' }: {
  cols: number; more: boolean; loading: boolean; failed: boolean; onMore: () => void; total: number; noun: string
  onClear?: () => void // set when filters or a search narrow the list, so an empty result can undo them
  clearLabel?: string
}) {
  const { ref, entry } = useIntersection({ rootMargin: '400px' })
  useEffect(() => { if (entry?.isIntersecting && more && !loading && !failed) onMore() }, [entry?.isIntersecting, more, loading, failed, onMore])
  return (
    <Table.Tr ref={ref}>
      <Table.Td colSpan={cols}>
        <Center py="xs" role="status">
          {failed && !loading ? (
            <Group gap="xs">
              <Text size="sm" c="red">Couldn’t load more {noun}. Check the server, then try again.</Text>
              <Button size="xs" variant="default" onClick={onMore}>Try Again</Button>
            </Group>
          ) : more || loading ? <Group gap="xs"><Loader size="sm" /><Text size="sm" c="dimmed">Loading {noun}…</Text></Group>
            : total ? <Text size="sm" c="dimmed">All {num(total)} {noun} shown</Text>
            : (
              <Stack gap={6} align="center" py="md">
                <Text size="sm" fw={500}>No {noun} match</Text>
                {onClear ? (
                  <>
                    <Text size="sm" c="dimmed">Remove a filter or change the search to widen the list.</Text>
                    <Button size="xs" variant="default" onClick={onClear}>{clearLabel}</Button>
                  </>
                ) : <Text size="sm" c="dimmed">Nothing has been recorded yet. Sources fill this in as they run.</Text>}
              </Stack>
            )}
        </Center>
      </Table.Td>
    </Table.Tr>
  )
}
