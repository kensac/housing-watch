import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

import { api, qs, type Meta, type Page, type SavedView, type ViewState } from './api'

export function useMeta() {
  return useQuery({ queryKey: ['meta'], queryFn: () => api.get<Meta>('/api/meta'), staleTime: Infinity })
}

export function useViews() {
  return useQuery({ queryKey: ['views'], queryFn: () => api.get<SavedView[]>('/api/views') })
}

export function useRefreshViews() {
  const qc = useQueryClient()
  return useCallback(() => qc.invalidateQueries({ queryKey: ['views'] }), [qc])
}

/** A list page's URL for a state (defaults left out), optionally inside saved view `view`. */
export function stateUrl(page: Page, st: ViewState, meta: Meta | undefined, view?: number | 'none' | null): string {
  const pm = meta?.pages[page]
  const sort = pm && st.sort === pm.default_sort ? '' : st.sort
  const cols = pm && st.columns.join(',') === pm.default_columns.join(',') ? '' : st.columns.join(',')
  return `/${page}${qs({ f: st.f, q: st.q.trim(), sort, cols, view: view ?? null })}`
}

export function sameState(a: ViewState, b: ViewState): boolean {
  return a.f.join('\n') === b.f.join('\n') && a.q.trim() === b.q.trim() && a.sort === b.sort &&
    a.columns.join(',') === b.columns.join(',')
}

/** The list state a page shows, all of it in the URL: filters, search, sort, columns, saved view, open row. */
export function useListState(page: Page) {
  const [params, setParams] = useSearchParams()
  const meta = useMeta().data
  const views = useViews().data
  const pm = meta?.pages[page]
  const state: ViewState = useMemo(() => ({
    f: params.getAll('f'),
    q: params.get('q') ?? '',
    sort: params.get('sort') ?? pm?.default_sort ?? '',
    columns: params.get('cols')?.split(',').filter(Boolean) ?? pm?.default_columns ?? [],
  }), [params, pm])
  const viewParam = params.get('view')
  const view = views?.find((v) => String(v.id) === viewParam && v.page === page) ?? null
  const open = params.get('open')

  const set = useCallback((patch: Partial<ViewState> & { open?: string | null }, replace = false) => {
    const next = { ...state, ...patch }
    const p = new URLSearchParams(stateUrl(page, next, meta, viewParam as 'none' | null).split('?')[1] ?? '')
    const o = 'open' in patch ? patch.open : open
    if (o) p.set('open', o)
    setParams(p, { replace })
  }, [state, page, meta, viewParam, open, setParams])

  return {
    state, view, open, set, ready: Boolean(meta && pm && views),
    modified: Boolean(view && !sameState(state, view.state)),
    bare: [...params.keys()].length === 0,
  }
}
