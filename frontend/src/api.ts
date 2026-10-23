// The GraphQL API of src/housing_watch/web/schema.py, at /graphql. Every document is written with `graphql()`,
// so its result and variables are typed from schema.graphql (`npm run codegen`); filters, sorts and columns are
// typed inputs too (filters.ts). This file holds the shared fragments and documents; the rest sit with their page.
import type { TypedDocumentNode } from '@graphql-typed-document-node/core'
import { print } from 'graphql'

import { graphql } from './gql'
import type {
  Borough, ChipFieldsFragment, EventFieldsFragment, Kind, ListingFieldsFragment, MetaQuery, Size, StageGroup,
  TrackerQuery, UnitFieldsFragment, ViewFieldsFragment,
} from './gql/graphql'

export type {
  Borough, EventColumn, EventFilterInput, EventType, FilterOp, Kind, ListingColumn, ListingFilterInput,
  ListingSortInput, Page, PageFilterInput, Size, SortDirection, StageGroup, Status, UnitColumn, UnitSortInput, Verdict,
} from './gql/graphql'
export type Listing = ListingFieldsFragment
export type Unit = UnitFieldsFragment
export type Event = EventFieldsFragment
export type Chip = ChipFieldsFragment
export type SavedView = ViewFieldsFragment
export type Meta = MetaQuery['meta']
export type Field = Meta['pages']['listings']['fields'][number]
export type TrackerRow = TrackerQuery['tracker']['rows'][number]
export type StageStep = TrackerRow['events'][number]

// GraphQL enum values are names; these are the data's own words for the ones that differ
export const STAGE_GROUP: Record<StageGroup, string> = { your_move: 'your move', their_move: 'their move', done: 'done' }
export const BOROUGH: Record<Borough, string> = {
  Manhattan: 'Manhattan', Brooklyn: 'Brooklyn', Queens: 'Queens', Bronx: 'Bronx', Staten_Island: 'Staten Island',
}
export const SIZE: Record<Size, string> = {
  studio: 'Studio', sro: 'SRO', _1br: '1BR', _2br: '2BR', _3br: '3BR', _4br: '4BR', _5br: '5BR', _6br: '6BR', unknown: 'Unknown',
}
export const KIND: Record<Kind, string> = { re_rental: 're-rental', lottery: 'lottery', waitlist: 'waitlist', homeownership: 'homeownership' }

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
  fragment ChipFields on Chip { field op label }
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

// Every operator of every filter type: a saved view's filter comes back whole (filters.ts checks the selections
// cover the schema's types, so a field added there cannot be dropped here unnoticed).
export const FilterFieldsDoc = graphql(`
  fragment TextFilterFields on TextFilter { in not_in contains not_contains empty }
  fragment NumberFilterFields on NumberFilter { eq ne lt le gt ge between { min max } empty }
  fragment TimeFilterFields on TimeFilter {
    after before between { from to } since until last { amount unit } empty
  }
  fragment BoolFilterFields on BoolFilter { is empty }
  fragment BandFilterFields on BandFilter {
    household { ...NumberFilterFields } income_min { ...NumberFilterFields } income_max { ...NumberFilterFields }
  }
  fragment UnitFilterFields on UnitFilter {
    size { in not_in empty } rent { ...NumberFilterFields } ami { ...NumberFilterFields }
    unit_no { ...TextFilterFields } set_aside { ...TextFilterFields } available { ...BoolFilterFields }
    chart_year { ...NumberFilterFields } bands { ...BandFilterFields }
  }
  fragment ListingFilterFields on ListingFilter {
    source { ...TextFilterFields } title { ...TextFilterFields } address { ...TextFilterFields }
    borough { in not_in empty } status { in not_in empty } kind { in not_in empty } deadline { ...TextFilterFields }
    senior_only { ...BoolFilterFields } notes { ...TextFilterFields } first_seen { ...TimeFilterFields }
    last_seen { ...TimeFilterFields } last_changed { ...TimeFilterFields } gone_since { ...TimeFilterFields }
    verdict { in not_in empty } reason { ...TextFilterFields } eligible_units { ...NumberFilterFields }
    unit_count { ...NumberFilterFields } commute { ...NumberFilterFields } application { ...TextFilterFields }
    application_group { in not_in empty } application_notes { ...TextFilterFields } units { ...UnitFilterFields }
  }
  fragment EventFilterFields on EventFilter {
    at { ...TimeFilterFields } source { ...TextFilterFields } type { in not_in empty }
    summary { ...TextFilterFields } notified { ...TimeFilterFields } first_run { ...BoolFilterFields }
    listing { ...ListingFilterFields }
  }
`)

// The pages' sort and column enums differ, so the union's members alias them (GraphQL's field merging rule).
export const ViewFieldsDoc = graphql(`
  fragment ViewFields on View {
    id page name position is_default warnings count updated
    state {
      __typename
      ... on ListingsViewState { q filter { ...ListingFilterFields } sort { field direction } columns }
      ... on UnitsViewState {
        q filter { ...ListingFilterFields } unit_sort: sort { field direction } unit_columns: columns
      }
      ... on EventsViewState { event_filter: filter { ...EventFilterFields } event_columns: columns }
    }
  }
`)

export const FieldFieldsDoc = graphql(`
  fragment FieldFields on FilterField { name path label group type money ops choices { name label } }
`)

// --- shared documents -----------------------------------------------------------------------------------

export const MetaDoc = graphql(`
  query Meta {
    meta {
      profile { household income sizes excluded_boroughs } statuses verdicts ops { op label }
      pages {
        listings {
          fields { ...FieldFields } columns { column label } default_columns
          sorts { field label natural } default_sort { field direction }
        }
        units {
          fields { ...FieldFields } columns { column label } default_columns
          sorts { field label natural } default_sort { field direction }
        }
        events { fields { ...FieldFields } columns { column label } default_columns }
      }
    }
  }
`)

export const ViewsDoc = graphql(`
  query Views { views { ...ViewFields } }
`)

export const ListingsDoc = graphql(`
  query Listings(
    $filter: ListingFilterInput, $q: String! = "", $sort: [ListingSortInput!], $first: Int! = 200, $after: String
  ) {
    listings(filter: $filter, q: $q, sort: $sort, first: $first, after: $after) {
      edges { node { ...ListingFields } }
      page_info { has_next_page end_cursor }
      total verdict_counts { eligible possible excluded } filters { ...ChipFields } hidden_gone
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
  mutation LinkTracker($listing_key: ID!, $row_key: ID) {
    linkTracker(input: { listing_key: $listing_key, row_key: $row_key }) { listing_key row_key }
  }
`)

export const UnlinkTrackerDoc = graphql(`
  mutation UnlinkTracker($listing_key: ID!) {
    unlinkTracker(input: { listing_key: $listing_key }) { listing_key row_key }
  }
`)
