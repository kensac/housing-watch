// The GraphQL API of src/housing_watch/web/schema.py, at /graphql. Every document is written with `graphql()`,
// so its result and variables are typed from schema.graphql (`npm run codegen`); filters travel as
// `f: ["<field>.<op>.<values>"]`. This file holds the shared fragments and documents; the rest sit with their page.
import type { TypedDocumentNode } from '@graphql-typed-document-node/core'
import { print } from 'graphql'

import { graphql } from './gql'
import type {
  ChipFieldsFragment, EventFieldsFragment, ListingFieldsFragment, MetaQuery, StageGroup, TrackerQuery,
  UnitFieldsFragment, ViewFieldsFragment,
} from './gql/graphql'

export type { Page, StageGroup, Verdict } from './gql/graphql'
export type Listing = ListingFieldsFragment
export type Unit = UnitFieldsFragment
export type Event = EventFieldsFragment
export type Chip = ChipFieldsFragment
export type SavedView = ViewFieldsFragment
export type ViewState = SavedView['state']
export type Meta = MetaQuery['meta']
export type Field = Meta['pages']['listings']['fields'][number]
export type TrackerRow = TrackerQuery['tracker']['rows'][number]
export type StageStep = TrackerRow['events'][number]

export const STAGE_GROUP: Record<StageGroup, string> = { your_move: 'your move', their_move: 'their move', done: 'done' }

/** A refused or failed request: the server's message, and its `extensions.code` when it sent one. */
export class ApiError extends Error {
  readonly code?: string
  constructor(message: string, code?: string) {
    super(message)
    this.code = code
  }
}

const printed = new WeakMap<object, string>()
type Vars<V> = V extends Record<string, never> ? [variables?: V] : [variables: V]

/** POST one document; its typed `data`, or the first error as an ApiError. */
export async function request<R, V>(doc: TypedDocumentNode<R, V>, ...[variables]: Vars<V>): Promise<R> {
  let query = printed.get(doc)
  if (query === undefined) printed.set(doc, (query = print(doc)))
  const r = await fetch('/graphql', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, variables }),
  })
  const res = await r.json().catch(() => ({ errors: [{ message: r.statusText }] }))
  const err = res.errors?.[0]
  if (err) throw new ApiError(err.message, err.extensions?.code)
  if (!r.ok) throw new ApiError(r.statusText)
  return res.data as R
}

export function qs(params: Record<string, string | number | string[] | null | undefined>): string {
  const u = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === '') continue
    for (const x of Array.isArray(v) ? v : [v]) u.append(k, String(x))
  }
  const s = u.toString()
  return s ? `?${s}` : ''
}

// --- shared fragments ---------------------------------------------------------------------------------

export const ChipFieldsDoc = graphql(`
  fragment ChipFields on Chip { i f label field op values }
`)

export const ListingFieldsDoc = graphql(`
  fragment ListingFields on Listing {
    key source title url address borough status kind deadline senior_only first_seen last_changed gone_since
    verdict reason units eligible_units sizes rent_min rent_max ami_min ami_max commute
    commute_route { lines walk_min transfers } application application_group application_since
    application_since_baseline application_notes tracker_row tracker_match
  }
`)

export const UnitFieldsDoc = graphql(`
  fragment UnitFields on Unit {
    id key source title url address borough status kind deadline first_seen last_changed gone_since listing_verdict
    size unit_no rent ami set_aside available chart_year income_min income_max verdict reason commute application
    application_group application_since application_since_baseline
  }
`)

export const EventFieldsDoc = graphql(`
  fragment EventFields on Event { id at source key type summary notified baseline title verdict }
`)

export const ViewFieldsDoc = graphql(`
  fragment ViewFields on View { id page name position is_default warnings count updated state { f q sort columns } }
`)

export const PageMetaFieldsDoc = graphql(`
  fragment PageMetaFields on PageMeta {
    fields { name label group type ops money choices } columns { key label } default_columns default_sort
    sorts { key label }
  }
`)

// --- shared documents -----------------------------------------------------------------------------------

export const MetaDoc = graphql(`
  query Meta {
    meta {
      profile { household income sizes excluded_boroughs } statuses verdicts ops { op label }
      pages { listings { ...PageMetaFields } units { ...PageMetaFields } events { ...PageMetaFields } }
    }
  }
`)

export const ViewsDoc = graphql(`
  query Views { views { ...ViewFields } }
`)

export const ListingsDoc = graphql(`
  query Listings($f: [String!], $q: String! = "", $sort: String! = "", $first: Int! = 200, $after: String) {
    listings(f: $f, q: $q, sort: $sort, first: $first, after: $after) {
      edges { node { ...ListingFields } }
      page_info { has_next_page end_cursor }
      total verdict_counts { eligible possible excluded } filters { ...ChipFields } problems hidden_gone
    }
  }
`)

export const TrackerDoc = graphql(`
  query Tracker {
    tracker {
      configured synced
      rows {
        row_key position property status stage group stage_since stage_since_baseline notes source_url page_url
        events { at stage_from stage_to group baseline } cells { name value }
        listings { key title verdict commute tracker_match }
      }
    }
  }
`)

export const LinkTrackerDoc = graphql(`
  mutation LinkTracker($listing_key: String!, $row_key: String) {
    linkTracker(input: { listing_key: $listing_key, row_key: $row_key }) { listing_key row_key }
  }
`)

export const UnlinkTrackerDoc = graphql(`
  mutation UnlinkTracker($listing_key: String!) {
    unlinkTracker(input: { listing_key: $listing_key }) { listing_key row_key }
  }
`)
