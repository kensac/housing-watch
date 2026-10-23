import { useDocumentTitle, useReducedMotion } from '@mantine/hooks'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useMemo, type MouseEvent } from 'react'
import { useSearchParams } from 'react-router-dom'

import { MetaDoc, request, TrackerDoc, ViewsDoc, type Page } from './api'
import { parseState, sameState, stateParams, viewState, type ListState, type Patch } from './filters'

export const TITLES: Record<Page, string> = { listings: 'Listings', units: 'Units', events: 'Events' }

export function useMeta() {
  return useQuery({ queryKey: ['meta'], queryFn: () => request(MetaDoc).then((d) => d.meta), staleTime: Infinity })
}

export function useViews() {
  return useQuery({ queryKey: ['views'], queryFn: () => request(ViewsDoc).then((d) => d.views) })
}

export function useRefreshViews() {
  const qc = useQueryClient()
  return useCallback(() => qc.invalidateQueries({ queryKey: ['views'] }), [qc])
}

/** Transition props that switch Mantine's motion off when the OS asks for reduced motion. */
export function useMotion() {
  return useReducedMotion() ? { duration: 0 } : undefined
}

export function useTitle(title: string | undefined) {
  useDocumentTitle(title ? `${title} · housing-watch` : 'housing-watch')
}

export type SetState = (patch: Patch & { open?: string | null }, replace?: boolean) => void

/** The list state a page shows, all of it in the URL: filter, search, sort, columns, saved view, open row.
 * `state` is null until the page's registry (meta) has loaded: it is what the URL is checked against. */
export function useListState(page: Page) {
  const [params, setParams] = useSearchParams()
  const meta = useMeta().data
  const views = useViews().data
  const parsed = useMemo(() => (meta ? parseState(page, params, meta) : null), [page, params, meta])
  const state: ListState | null = parsed?.state ?? null
  const viewParam = params.get('view')
  const view = views?.find((v) => v.id === viewParam && v.page === page) ?? null
  const open = params.get('open')

  const set: SetState = useCallback((patch, replace = false) => {
    const cur: Patch = state ? { ...state } : {}
    const p = stateParams(page, { ...cur, ...patch }, meta)
    if (viewParam) p.set('view', viewParam)
    const o = 'open' in patch ? patch.open : open
    if (o) p.set('open', o)
    setParams(p, { replace })
  }, [state, page, meta, viewParam, open, setParams])

  // the list's own query, without `open`: stable while the drawer opens and closes, so rows can skip re-rendering
  const base = useMemo(() => {
    const p = state ? stateParams(page, state, meta) : new URLSearchParams()
    if (viewParam) p.set('view', viewParam)
    return p.toString()
  }, [page, state, meta, viewParam])

  return {
    state, view, open, set, base, ready: Boolean(meta && views && state),
    unreadable: Boolean(parsed?.unreadable),
    modified: Boolean(view && state && !sameState(state, viewState(view), meta)),
    bare: [...params.keys()].length === 0,
  }
}

/** The link that opens listing `key` in the drawer over a list whose query is `base`. */
export const openHref = (page: Page | '', base: string, key: string) =>
  `/${page}?${base ? `${base}&` : ''}open=${encodeURIComponent(key)}`

/** A table row's mouse shortcut to its link: plain clicks on the row open it; clicks on controls, modified
 * clicks (new tab) and text selections are left alone. Keyboard users reach the row's real link instead. */
export function rowClick(go: () => void) {
  return (e: MouseEvent<HTMLElement>) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    if (e.target instanceof Element && e.target.closest('a, button, input, label, [role="button"]')) return
    if (window.getSelection()?.toString()) return
    go()
  }
}

export function useTracker() {
  return useQuery({ queryKey: ['tracker'], queryFn: () => request(TrackerDoc).then((d) => d.tracker) })
}

/** Re-read everything a tracker link changes: the listing, the lists and the tracker page. */
export function useTrackerRefresh() {
  const qc = useQueryClient()
  return () => Promise.all(['listing', 'listings', 'units', 'tracker', 'overview'].map((k) => qc.invalidateQueries({ queryKey: [k] })))
}
