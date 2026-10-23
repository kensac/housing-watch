import { Alert, Button, Center, Flex, Group, Loader, Stack, Table, Text, UnstyledButton } from '@mantine/core'
import { useIntersection } from '@mantine/hooks'
import { IconAlertTriangle, IconArrowDown, IconArrowUp, IconArrowsSort, IconListSearch } from '@tabler/icons-react'
import { memo, useEffect, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'

import type { Chip, Page } from '../api'
import { stateUrl, viewState, type ListState } from '../filters'
import { num } from '../format'
import { openHref, rowClick, TITLES, useListState, useMeta, useTitle, useViews, type SetState } from '../hooks'
import { FilterBar } from './FilterBar'
import classes from './ListPage.module.css'
import { Loading, LoadError } from './State'
import { SearchInput, Toolbar } from './Toolbar'
import { ViewBar } from './ViewBar'

export type ListCtx = ReturnType<typeof useListState>
/** What a page's table renders from: the state, known to have loaded. */
export type ReadyCtx = Omit<ListCtx, 'state'> & { state: ListState }

// From sm up, a list page fills the viewport and its table scrolls inside it, so the header row stays in view;
// on a phone the page scrolls as a whole.
const FILL = { base: 'auto', sm: 'calc(100dvh - var(--app-shell-header-offset, 0rem) - 2 * var(--app-shell-padding))' }

/** A list page: its saved view, toolbar and filters, then whatever table `children` renders for the state. */
export function ListPage({ page, chips, summary, children, search = true, error, retry }: {
  page: Page; chips: Chip[]; summary: ReactNode; search?: boolean
  error?: unknown; retry?: () => void
  children: (ctx: ReadyCtx) => ReactNode
}) {
  const ctx = useListState(page)
  const meta = useMeta()
  const views = useViews()
  const navigate = useNavigate()
  useTitle(ctx.view?.name ?? TITLES[page])
  // a bare page URL opens the page's default view, if the user made one
  const dflt = views.data?.find((v) => v.page === page && v.is_default)
  useEffect(() => {
    if (ctx.bare && dflt) navigate(stateUrl(viewState(dflt), meta.data, dflt.id), { replace: true })
  }, [ctx.bare, dflt, meta.data, navigate, page])
  const setupError = meta.error ?? views.error
  if (setupError) return <LoadError what="the page setup" error={setupError} retry={() => { meta.refetch(); views.refetch() }} />
  const state = ctx.state
  if (!ctx.ready || !state) return <Loading rows={10} label={`Loading ${TITLES[page].toLowerCase()}…`} />
  return (
    <Stack gap={0} h={FILL}>
      <ViewBar state={state} view={ctx.view} modified={ctx.modified} />
      {/* search and filters on the left, sort and columns on the right; stacked on a phone */}
      <Flex direction={{ base: 'column', sm: 'row' }} justify="space-between" align={{ base: 'stretch', sm: 'flex-start' }} gap="sm" mt="md">
        <Group gap="xs" style={{ flex: 1, minWidth: 0 }}>
          {search && state.page !== 'events' && <SearchInput page={page} q={state.q} set={ctx.set} />}
          <FilterBar page={page} filter={state.filter} chips={chips} q={state.page === 'events' ? '' : state.q}
            onChange={(filter) => ctx.set({ filter })} />
        </Group>
        <Toolbar state={state} set={ctx.set} />
      </Flex>
      {ctx.unreadable && (
        <Alert mt="sm" color="orange" variant="light" icon={<IconAlertTriangle size={16} aria-hidden />}
          title="This link’s filter could not be read">It names a field, operator or value this page does not have, so the list is unfiltered.</Alert>
      )}
      <Text size="xs" c="dimmed" aria-live="polite" mt="sm" mb="xs">{summary}</Text>
      {error ? <LoadError what={TITLES[page].toLowerCase()} error={error} retry={retry} /> : children({ ...ctx, state })}
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

export type Cells<T, C extends string = string, S extends string = string> = Record<C, { label: string; sort?: S; right?: boolean; cell: (x: T) => ReactNode }>

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
        className={dim?.(x) ? classes.dim : undefined}>
        {lead(x, to)}
        {shown.map((c) => <Table.Td key={c} ta={cells[c].right ? 'right' : undefined} style={{ whiteSpace: 'nowrap' }}>{cells[c].cell(x)}</Table.Td>)}
      </Table.Tr>
    )
  })
}) as <T>(p: RowsProps<T>) => ReactNode

/** The leading cell of a list row: a title line over a muted detail line; it gets whatever width the other columns leave. */
export const LeadTd = ({ children }: { children: ReactNode }) => <Table.Td className={classes.lead}>{children}</Table.Td>

/** The dense, sticky-header table every list page uses; dimmed while a changed query is loading. */
export function ListTable({ label, minWidth, stale, children }: { label: string; minWidth: number; stale: boolean; children: ReactNode }) {
  return (
    <Table.ScrollContainer type="native" minWidth={minWidth} flex={1} mih={0} style={{ scrollPaddingTop: 40 }}>
      <Table stickyHeader highlightOnHover tabularNums fz="sm" className={classes.table} aria-label={label} aria-busy={stale}>
        {children}
      </Table>
    </Table.ScrollContainer>
  )
}

/** A column header that sorts by `sort` (in its natural direction; click again to reverse); `lead` marks the column
 * that takes the slack. */
export function SortTh({ label, sort, state, set, ta, lead }: {
  label: string; sort?: string; state?: ListState; set?: SetState; ta?: 'right'; lead?: boolean
}) {
  const meta = useMeta().data
  const className = lead ? classes.lead : undefined
  if (!sort || !state || !set || state.page === 'events') return <Table.Th ta={ta} className={className}>{label}</Table.Th>
  const first = state.sort[0]
  const on = first?.field === sort
  const desc = on && first?.direction === 'desc'
  const natural = meta?.pages[state.page].sorts.find((s) => s.field === sort)?.natural ?? 'asc'
  const Icon = !on ? IconArrowsSort : desc ? IconArrowDown : IconArrowUp
  return (
    <Table.Th ta={ta} className={className} aria-sort={on ? (desc ? 'descending' : 'ascending') : 'none'}>
      <UnstyledButton className={classes.sort} data-on={on || undefined} data-right={ta === 'right' || undefined}
        onClick={() => set({ sort: [{ field: sort, direction: on ? (desc ? 'asc' : 'desc') : natural }] })}>
        {label}<Icon size={14} className={classes.sortIcon} aria-hidden />
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
        <Center py="sm" role="status">
          {failed && !loading ? (
            <Group gap="sm">
              <Text size="sm" c="red.8">Couldn’t load more {noun}. Check the server, then try again.</Text>
              <Button size="xs" variant="default" onClick={onMore}>Try Again</Button>
            </Group>
          ) : more || loading ? <Group gap="xs"><Loader size="xs" /><Text size="sm" c="dimmed">Loading {noun}…</Text></Group>
            : total ? <Text size="sm" c="dimmed">All {num(total)} {noun} shown</Text>
            : (
              <Stack gap={4} align="center" py="xl">
                <IconListSearch size={22} className={classes.emptyIcon} aria-hidden />
                <Text size="sm" fw={500} mt={4}>No {noun} match</Text>
                {onClear ? (
                  <>
                    <Text size="sm" c="dimmed">Remove a filter or change the search to widen the list.</Text>
                    <Button size="xs" variant="default" mt="xs" onClick={onClear}>{clearLabel}</Button>
                  </>
                ) : <Text size="sm" c="dimmed">Nothing has been recorded yet. Sources fill this in as they run.</Text>}
              </Stack>
            )}
        </Center>
      </Table.Td>
    </Table.Tr>
  )
}
