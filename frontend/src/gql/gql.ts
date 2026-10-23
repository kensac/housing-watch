/* eslint-disable */
import * as types from './graphql';
import type { TypedDocumentNode as DocumentNode } from '@graphql-typed-document-node/core';

/**
 * Map of all GraphQL operations in the project.
 *
 * This map has several performance disadvantages:
 * 1. It is not tree-shakeable, so it will include all operations in the project.
 * 2. It is not minifiable, so the string of a GraphQL query will be multiple times inside the bundle.
 * 3. It does not support dead code elimination, so it will add unused operations.
 *
 * Therefore it is highly recommended to use the babel or swc plugin for production.
 * Learn more about it here: https://the-guild.dev/graphql/codegen/plugins/presets/preset-client#reducing-bundle-size
 */
type Documents = {
    "\n  fragment ChipFields on Chip { i f label field op values }\n": typeof types.ChipFieldsFragmentDoc,
    "\n  fragment ListingFields on Listing {\n    key source title url address borough status kind deadline senior_only first_seen last_changed gone_since\n    verdict reason units eligible_units sizes rent_min rent_max ami_min ami_max commute\n    commute_route { lines walk_min transfers } application application_group application_since\n    application_since_baseline application_notes tracker_row tracker_match\n  }\n": typeof types.ListingFieldsFragmentDoc,
    "\n  fragment UnitFields on Unit {\n    id key source title url address borough status kind deadline first_seen last_changed gone_since listing_verdict\n    size unit_no rent ami set_aside available chart_year income_min income_max verdict reason commute application\n    application_group application_since application_since_baseline\n  }\n": typeof types.UnitFieldsFragmentDoc,
    "\n  fragment EventFields on Event { id at source key type summary notified baseline title verdict }\n": typeof types.EventFieldsFragmentDoc,
    "\n  fragment ViewFields on View { id page name position is_default warnings count updated state { f q sort columns } }\n": typeof types.ViewFieldsFragmentDoc,
    "\n  fragment PageMetaFields on PageMeta {\n    fields { name label group type ops money choices } columns { key label } default_columns default_sort\n    sorts { key label }\n  }\n": typeof types.PageMetaFieldsFragmentDoc,
    "\n  query Meta {\n    meta {\n      profile { household income sizes excluded_boroughs } statuses verdicts ops { op label }\n      pages { listings { ...PageMetaFields } units { ...PageMetaFields } events { ...PageMetaFields } }\n    }\n  }\n": typeof types.MetaDocument,
    "\n  query Views { views { ...ViewFields } }\n": typeof types.ViewsDocument,
    "\n  query Listings($f: [String!], $q: String! = \"\", $sort: String! = \"\", $first: Int! = 200, $after: String) {\n    listings(f: $f, q: $q, sort: $sort, first: $first, after: $after) {\n      edges { node { ...ListingFields } }\n      page_info { has_next_page end_cursor }\n      total verdict_counts { eligible possible excluded } filters { ...ChipFields } problems hidden_gone\n    }\n  }\n": typeof types.ListingsDocument,
    "\n  query Tracker {\n    tracker {\n      configured synced\n      rows {\n        row_key position property status stage group stage_since stage_since_baseline notes source_url page_url\n        events { at stage_from stage_to group baseline } cells { name value }\n        listings { key title verdict commute tracker_match }\n      }\n    }\n  }\n": typeof types.TrackerDocument,
    "\n  mutation LinkTracker($listing_key: String!, $row_key: String) {\n    linkTracker(input: { listing_key: $listing_key, row_key: $row_key }) { listing_key row_key }\n  }\n": typeof types.LinkTrackerDocument,
    "\n  mutation UnlinkTracker($listing_key: String!) {\n    unlinkTracker(input: { listing_key: $listing_key }) { listing_key row_key }\n  }\n": typeof types.UnlinkTrackerDocument,
    "\n  query Values($page: Page!, $field: String!, $f: [String!], $q: String! = \"\") {\n    values(page: $page, field: $field, f: $f, q: $q) { enum counts { value count } }\n  }\n": typeof types.ValuesDocument,
    "\n  query Listing($key: String!) {\n    listing(key: $key) {\n      summary { ...ListingFields } notes ext_id last_seen household pdfs\n      units { size unit_no rent ami set_aside available bands { household income_min income_max } verdict reason chart_year }\n      events { ...EventFields }\n    }\n  }\n": typeof types.ListingDocument,
    "\n  query ListingApplications($key: String!) {\n    listingApplications(key: $key) { ready pdfs { n url form error } }\n  }\n": typeof types.ListingApplicationsDocument,
    "\n  mutation CreateView($input: CreateViewInput!) { createView(input: $input) { view { ...ViewFields } } }\n": typeof types.CreateViewDocument,
    "\n  mutation ChangeView($input: ChangeViewInput!) { changeView(input: $input) { view { ...ViewFields } } }\n": typeof types.ChangeViewDocument,
    "\n  mutation DuplicateView($id: Int!) { duplicateView(input: { id: $id }) { view { ...ViewFields } } }\n": typeof types.DuplicateViewDocument,
    "\n  mutation DeleteView($id: Int!) { deleteView(input: { id: $id }) { id } }\n": typeof types.DeleteViewDocument,
    "\n  query Events($f: [String!], $after: String) {\n    events(f: $f, after: $after) {\n      edges { node { ...EventFields } }\n      page_info { has_next_page end_cursor }\n      total filters { ...ChipFields } problems\n    }\n  }\n": typeof types.EventsDocument,
    "\n  query Fleet {\n    fleet {\n      available workers { name queues concurrency started last_seen age doing } queues { queue status count }\n      failures { id queue_name task_name args attempts at error } hosts { name last_run runs_24h fails_24h sources }\n    }\n  }\n": typeof types.FleetDocument,
    "\n  query Overview {\n    overview {\n      matrix { verdict status count } eligible_open eligible_live new_24h new_7d baseline_24h changed_24h changed_7d\n      failing last_digest pending eligible_now { ...ListingFields } recent { ...EventFields } total n_sources\n      digest { subject text events }\n    }\n  }\n": typeof types.OverviewDocument,
    "\n  query Sources {\n    sources {\n      source title url every_hours health last_ok last_attempt failing_since consecutive_failures next_due\n      last_duration last_error last_events listings eligible possible workers\n    }\n  }\n": typeof types.SourcesDocument,
    "\n  query Units($f: [String!], $q: String! = \"\", $sort: String! = \"\", $after: String) {\n    units(f: $f, q: $q, sort: $sort, after: $after) {\n      edges { node { ...UnitFields } }\n      page_info { has_next_page end_cursor }\n      total verdict_counts { eligible possible excluded } filters { ...ChipFields } problems hidden_gone\n    }\n  }\n": typeof types.UnitsDocument,
};
const documents: Documents = {
    "\n  fragment ChipFields on Chip { i f label field op values }\n": types.ChipFieldsFragmentDoc,
    "\n  fragment ListingFields on Listing {\n    key source title url address borough status kind deadline senior_only first_seen last_changed gone_since\n    verdict reason units eligible_units sizes rent_min rent_max ami_min ami_max commute\n    commute_route { lines walk_min transfers } application application_group application_since\n    application_since_baseline application_notes tracker_row tracker_match\n  }\n": types.ListingFieldsFragmentDoc,
    "\n  fragment UnitFields on Unit {\n    id key source title url address borough status kind deadline first_seen last_changed gone_since listing_verdict\n    size unit_no rent ami set_aside available chart_year income_min income_max verdict reason commute application\n    application_group application_since application_since_baseline\n  }\n": types.UnitFieldsFragmentDoc,
    "\n  fragment EventFields on Event { id at source key type summary notified baseline title verdict }\n": types.EventFieldsFragmentDoc,
    "\n  fragment ViewFields on View { id page name position is_default warnings count updated state { f q sort columns } }\n": types.ViewFieldsFragmentDoc,
    "\n  fragment PageMetaFields on PageMeta {\n    fields { name label group type ops money choices } columns { key label } default_columns default_sort\n    sorts { key label }\n  }\n": types.PageMetaFieldsFragmentDoc,
    "\n  query Meta {\n    meta {\n      profile { household income sizes excluded_boroughs } statuses verdicts ops { op label }\n      pages { listings { ...PageMetaFields } units { ...PageMetaFields } events { ...PageMetaFields } }\n    }\n  }\n": types.MetaDocument,
    "\n  query Views { views { ...ViewFields } }\n": types.ViewsDocument,
    "\n  query Listings($f: [String!], $q: String! = \"\", $sort: String! = \"\", $first: Int! = 200, $after: String) {\n    listings(f: $f, q: $q, sort: $sort, first: $first, after: $after) {\n      edges { node { ...ListingFields } }\n      page_info { has_next_page end_cursor }\n      total verdict_counts { eligible possible excluded } filters { ...ChipFields } problems hidden_gone\n    }\n  }\n": types.ListingsDocument,
    "\n  query Tracker {\n    tracker {\n      configured synced\n      rows {\n        row_key position property status stage group stage_since stage_since_baseline notes source_url page_url\n        events { at stage_from stage_to group baseline } cells { name value }\n        listings { key title verdict commute tracker_match }\n      }\n    }\n  }\n": types.TrackerDocument,
    "\n  mutation LinkTracker($listing_key: String!, $row_key: String) {\n    linkTracker(input: { listing_key: $listing_key, row_key: $row_key }) { listing_key row_key }\n  }\n": types.LinkTrackerDocument,
    "\n  mutation UnlinkTracker($listing_key: String!) {\n    unlinkTracker(input: { listing_key: $listing_key }) { listing_key row_key }\n  }\n": types.UnlinkTrackerDocument,
    "\n  query Values($page: Page!, $field: String!, $f: [String!], $q: String! = \"\") {\n    values(page: $page, field: $field, f: $f, q: $q) { enum counts { value count } }\n  }\n": types.ValuesDocument,
    "\n  query Listing($key: String!) {\n    listing(key: $key) {\n      summary { ...ListingFields } notes ext_id last_seen household pdfs\n      units { size unit_no rent ami set_aside available bands { household income_min income_max } verdict reason chart_year }\n      events { ...EventFields }\n    }\n  }\n": types.ListingDocument,
    "\n  query ListingApplications($key: String!) {\n    listingApplications(key: $key) { ready pdfs { n url form error } }\n  }\n": types.ListingApplicationsDocument,
    "\n  mutation CreateView($input: CreateViewInput!) { createView(input: $input) { view { ...ViewFields } } }\n": types.CreateViewDocument,
    "\n  mutation ChangeView($input: ChangeViewInput!) { changeView(input: $input) { view { ...ViewFields } } }\n": types.ChangeViewDocument,
    "\n  mutation DuplicateView($id: Int!) { duplicateView(input: { id: $id }) { view { ...ViewFields } } }\n": types.DuplicateViewDocument,
    "\n  mutation DeleteView($id: Int!) { deleteView(input: { id: $id }) { id } }\n": types.DeleteViewDocument,
    "\n  query Events($f: [String!], $after: String) {\n    events(f: $f, after: $after) {\n      edges { node { ...EventFields } }\n      page_info { has_next_page end_cursor }\n      total filters { ...ChipFields } problems\n    }\n  }\n": types.EventsDocument,
    "\n  query Fleet {\n    fleet {\n      available workers { name queues concurrency started last_seen age doing } queues { queue status count }\n      failures { id queue_name task_name args attempts at error } hosts { name last_run runs_24h fails_24h sources }\n    }\n  }\n": types.FleetDocument,
    "\n  query Overview {\n    overview {\n      matrix { verdict status count } eligible_open eligible_live new_24h new_7d baseline_24h changed_24h changed_7d\n      failing last_digest pending eligible_now { ...ListingFields } recent { ...EventFields } total n_sources\n      digest { subject text events }\n    }\n  }\n": types.OverviewDocument,
    "\n  query Sources {\n    sources {\n      source title url every_hours health last_ok last_attempt failing_since consecutive_failures next_due\n      last_duration last_error last_events listings eligible possible workers\n    }\n  }\n": types.SourcesDocument,
    "\n  query Units($f: [String!], $q: String! = \"\", $sort: String! = \"\", $after: String) {\n    units(f: $f, q: $q, sort: $sort, after: $after) {\n      edges { node { ...UnitFields } }\n      page_info { has_next_page end_cursor }\n      total verdict_counts { eligible possible excluded } filters { ...ChipFields } problems hidden_gone\n    }\n  }\n": types.UnitsDocument,
};

/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 *
 *
 * @example
 * ```ts
 * const query = graphql(`query GetUser($id: ID!) { user(id: $id) { name } }`);
 * ```
 *
 * The query argument is unknown!
 * Please regenerate the types.
 */
export function graphql(source: string): unknown;

/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "\n  fragment ChipFields on Chip { i f label field op values }\n"): (typeof documents)["\n  fragment ChipFields on Chip { i f label field op values }\n"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "\n  fragment ListingFields on Listing {\n    key source title url address borough status kind deadline senior_only first_seen last_changed gone_since\n    verdict reason units eligible_units sizes rent_min rent_max ami_min ami_max commute\n    commute_route { lines walk_min transfers } application application_group application_since\n    application_since_baseline application_notes tracker_row tracker_match\n  }\n"): (typeof documents)["\n  fragment ListingFields on Listing {\n    key source title url address borough status kind deadline senior_only first_seen last_changed gone_since\n    verdict reason units eligible_units sizes rent_min rent_max ami_min ami_max commute\n    commute_route { lines walk_min transfers } application application_group application_since\n    application_since_baseline application_notes tracker_row tracker_match\n  }\n"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "\n  fragment UnitFields on Unit {\n    id key source title url address borough status kind deadline first_seen last_changed gone_since listing_verdict\n    size unit_no rent ami set_aside available chart_year income_min income_max verdict reason commute application\n    application_group application_since application_since_baseline\n  }\n"): (typeof documents)["\n  fragment UnitFields on Unit {\n    id key source title url address borough status kind deadline first_seen last_changed gone_since listing_verdict\n    size unit_no rent ami set_aside available chart_year income_min income_max verdict reason commute application\n    application_group application_since application_since_baseline\n  }\n"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "\n  fragment EventFields on Event { id at source key type summary notified baseline title verdict }\n"): (typeof documents)["\n  fragment EventFields on Event { id at source key type summary notified baseline title verdict }\n"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "\n  fragment ViewFields on View { id page name position is_default warnings count updated state { f q sort columns } }\n"): (typeof documents)["\n  fragment ViewFields on View { id page name position is_default warnings count updated state { f q sort columns } }\n"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "\n  fragment PageMetaFields on PageMeta {\n    fields { name label group type ops money choices } columns { key label } default_columns default_sort\n    sorts { key label }\n  }\n"): (typeof documents)["\n  fragment PageMetaFields on PageMeta {\n    fields { name label group type ops money choices } columns { key label } default_columns default_sort\n    sorts { key label }\n  }\n"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "\n  query Meta {\n    meta {\n      profile { household income sizes excluded_boroughs } statuses verdicts ops { op label }\n      pages { listings { ...PageMetaFields } units { ...PageMetaFields } events { ...PageMetaFields } }\n    }\n  }\n"): (typeof documents)["\n  query Meta {\n    meta {\n      profile { household income sizes excluded_boroughs } statuses verdicts ops { op label }\n      pages { listings { ...PageMetaFields } units { ...PageMetaFields } events { ...PageMetaFields } }\n    }\n  }\n"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "\n  query Views { views { ...ViewFields } }\n"): (typeof documents)["\n  query Views { views { ...ViewFields } }\n"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "\n  query Listings($f: [String!], $q: String! = \"\", $sort: String! = \"\", $first: Int! = 200, $after: String) {\n    listings(f: $f, q: $q, sort: $sort, first: $first, after: $after) {\n      edges { node { ...ListingFields } }\n      page_info { has_next_page end_cursor }\n      total verdict_counts { eligible possible excluded } filters { ...ChipFields } problems hidden_gone\n    }\n  }\n"): (typeof documents)["\n  query Listings($f: [String!], $q: String! = \"\", $sort: String! = \"\", $first: Int! = 200, $after: String) {\n    listings(f: $f, q: $q, sort: $sort, first: $first, after: $after) {\n      edges { node { ...ListingFields } }\n      page_info { has_next_page end_cursor }\n      total verdict_counts { eligible possible excluded } filters { ...ChipFields } problems hidden_gone\n    }\n  }\n"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "\n  query Tracker {\n    tracker {\n      configured synced\n      rows {\n        row_key position property status stage group stage_since stage_since_baseline notes source_url page_url\n        events { at stage_from stage_to group baseline } cells { name value }\n        listings { key title verdict commute tracker_match }\n      }\n    }\n  }\n"): (typeof documents)["\n  query Tracker {\n    tracker {\n      configured synced\n      rows {\n        row_key position property status stage group stage_since stage_since_baseline notes source_url page_url\n        events { at stage_from stage_to group baseline } cells { name value }\n        listings { key title verdict commute tracker_match }\n      }\n    }\n  }\n"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "\n  mutation LinkTracker($listing_key: String!, $row_key: String) {\n    linkTracker(input: { listing_key: $listing_key, row_key: $row_key }) { listing_key row_key }\n  }\n"): (typeof documents)["\n  mutation LinkTracker($listing_key: String!, $row_key: String) {\n    linkTracker(input: { listing_key: $listing_key, row_key: $row_key }) { listing_key row_key }\n  }\n"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "\n  mutation UnlinkTracker($listing_key: String!) {\n    unlinkTracker(input: { listing_key: $listing_key }) { listing_key row_key }\n  }\n"): (typeof documents)["\n  mutation UnlinkTracker($listing_key: String!) {\n    unlinkTracker(input: { listing_key: $listing_key }) { listing_key row_key }\n  }\n"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "\n  query Values($page: Page!, $field: String!, $f: [String!], $q: String! = \"\") {\n    values(page: $page, field: $field, f: $f, q: $q) { enum counts { value count } }\n  }\n"): (typeof documents)["\n  query Values($page: Page!, $field: String!, $f: [String!], $q: String! = \"\") {\n    values(page: $page, field: $field, f: $f, q: $q) { enum counts { value count } }\n  }\n"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "\n  query Listing($key: String!) {\n    listing(key: $key) {\n      summary { ...ListingFields } notes ext_id last_seen household pdfs\n      units { size unit_no rent ami set_aside available bands { household income_min income_max } verdict reason chart_year }\n      events { ...EventFields }\n    }\n  }\n"): (typeof documents)["\n  query Listing($key: String!) {\n    listing(key: $key) {\n      summary { ...ListingFields } notes ext_id last_seen household pdfs\n      units { size unit_no rent ami set_aside available bands { household income_min income_max } verdict reason chart_year }\n      events { ...EventFields }\n    }\n  }\n"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "\n  query ListingApplications($key: String!) {\n    listingApplications(key: $key) { ready pdfs { n url form error } }\n  }\n"): (typeof documents)["\n  query ListingApplications($key: String!) {\n    listingApplications(key: $key) { ready pdfs { n url form error } }\n  }\n"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "\n  mutation CreateView($input: CreateViewInput!) { createView(input: $input) { view { ...ViewFields } } }\n"): (typeof documents)["\n  mutation CreateView($input: CreateViewInput!) { createView(input: $input) { view { ...ViewFields } } }\n"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "\n  mutation ChangeView($input: ChangeViewInput!) { changeView(input: $input) { view { ...ViewFields } } }\n"): (typeof documents)["\n  mutation ChangeView($input: ChangeViewInput!) { changeView(input: $input) { view { ...ViewFields } } }\n"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "\n  mutation DuplicateView($id: Int!) { duplicateView(input: { id: $id }) { view { ...ViewFields } } }\n"): (typeof documents)["\n  mutation DuplicateView($id: Int!) { duplicateView(input: { id: $id }) { view { ...ViewFields } } }\n"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "\n  mutation DeleteView($id: Int!) { deleteView(input: { id: $id }) { id } }\n"): (typeof documents)["\n  mutation DeleteView($id: Int!) { deleteView(input: { id: $id }) { id } }\n"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "\n  query Events($f: [String!], $after: String) {\n    events(f: $f, after: $after) {\n      edges { node { ...EventFields } }\n      page_info { has_next_page end_cursor }\n      total filters { ...ChipFields } problems\n    }\n  }\n"): (typeof documents)["\n  query Events($f: [String!], $after: String) {\n    events(f: $f, after: $after) {\n      edges { node { ...EventFields } }\n      page_info { has_next_page end_cursor }\n      total filters { ...ChipFields } problems\n    }\n  }\n"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "\n  query Fleet {\n    fleet {\n      available workers { name queues concurrency started last_seen age doing } queues { queue status count }\n      failures { id queue_name task_name args attempts at error } hosts { name last_run runs_24h fails_24h sources }\n    }\n  }\n"): (typeof documents)["\n  query Fleet {\n    fleet {\n      available workers { name queues concurrency started last_seen age doing } queues { queue status count }\n      failures { id queue_name task_name args attempts at error } hosts { name last_run runs_24h fails_24h sources }\n    }\n  }\n"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "\n  query Overview {\n    overview {\n      matrix { verdict status count } eligible_open eligible_live new_24h new_7d baseline_24h changed_24h changed_7d\n      failing last_digest pending eligible_now { ...ListingFields } recent { ...EventFields } total n_sources\n      digest { subject text events }\n    }\n  }\n"): (typeof documents)["\n  query Overview {\n    overview {\n      matrix { verdict status count } eligible_open eligible_live new_24h new_7d baseline_24h changed_24h changed_7d\n      failing last_digest pending eligible_now { ...ListingFields } recent { ...EventFields } total n_sources\n      digest { subject text events }\n    }\n  }\n"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "\n  query Sources {\n    sources {\n      source title url every_hours health last_ok last_attempt failing_since consecutive_failures next_due\n      last_duration last_error last_events listings eligible possible workers\n    }\n  }\n"): (typeof documents)["\n  query Sources {\n    sources {\n      source title url every_hours health last_ok last_attempt failing_since consecutive_failures next_due\n      last_duration last_error last_events listings eligible possible workers\n    }\n  }\n"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "\n  query Units($f: [String!], $q: String! = \"\", $sort: String! = \"\", $after: String) {\n    units(f: $f, q: $q, sort: $sort, after: $after) {\n      edges { node { ...UnitFields } }\n      page_info { has_next_page end_cursor }\n      total verdict_counts { eligible possible excluded } filters { ...ChipFields } problems hidden_gone\n    }\n  }\n"): (typeof documents)["\n  query Units($f: [String!], $q: String! = \"\", $sort: String! = \"\", $after: String) {\n    units(f: $f, q: $q, sort: $sort, after: $after) {\n      edges { node { ...UnitFields } }\n      page_info { has_next_page end_cursor }\n      total verdict_counts { eligible possible excluded } filters { ...ChipFields } problems hidden_gone\n    }\n  }\n"];

export function graphql(source: string) {
  return (documents as any)[source] ?? {};
}

export type DocumentType<TDocumentNode extends DocumentNode<any, any>> = TDocumentNode extends DocumentNode<  infer TType,  any>  ? TType  : never;