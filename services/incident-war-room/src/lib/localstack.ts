/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        lib/localstack.ts
 * Purpose::         Turns the `s3://` URI the worker logs into a URL the visitor's browser can
 *                   actually open.
 * Interacts With:   components/ExecutionTerminal.tsx, components/PostmortemModal.tsx, localstack (:4566)
 *
 * Curriculum Project: Project 3 — Asynchronous Queues & Cloud Operations
 * Skills:           Cloud Object Linking, Module Boundaries
 * Tools:            TypeScript
 *
 * Two components need this — the terminal linkifies the URI in place, the modal offers it as a
 * download — so it lives here rather than being exported from whichever component happened to need
 * it first. A component importing a constant from a sibling component is a dependency between
 * *views*, which is the kind of edge that turns a component graph into a knot.
 */

import { BucketName } from '../types/contracts.gen'

/**
 * Where the browser reaches the archived objects.
 *
 * Relative by default, served by the war room's own nginx at `/s3/` — because this URL is resolved
 * by the *visitor's* browser. The original default was `http://localhost:4566`, which is correct
 * exactly once: when the viewer is sitting at the machine running the stack. On a single-VM
 * deployment "localhost" is the viewer's own laptop, and the postmortem link — one of the two
 * things `spa-design-guidelines.md` §9 offers as proof the S3 archive is real — was dead.
 *
 * A same-origin path is correct on localhost, on a VM, and behind a reverse proxy on a subpath,
 * with no build-time configuration. `VITE_LOCALSTACK_URL` still overrides it for a deployment that
 * publishes LocalStack somewhere else and would rather not proxy.
 */
export const LOCALSTACK_URL = (import.meta.env.VITE_LOCALSTACK_URL || '/s3').replace(/\/$/, '')

/** Matches the postmortem URI the worker logs. Bucket name from the contract, never retyped. */
const S3_URI_PATTERN = new RegExp(`s3://${BucketName.POSTMORTEMS}/([A-Za-z0-9._\\-]+)`)

export interface PostmortemLink {
  /** The matched `s3://…` text, so an anchor can wrap exactly it. */
  uri: string
  key: string
  href: string
  /** The message text before and after the URI, for rendering around the anchor. */
  before: string
  after: string
}

/** Builds the browser URL for a postmortem object key. */
export function postmortemHref(objectKey: string): string {
  return `${LOCALSTACK_URL}/${BucketName.POSTMORTEMS}/${objectKey}`
}

/**
 * Finds a postmortem URI in a worker log line.
 *
 * Returns null when the line carries none, which is most of them — the caller renders plain text in
 * that case rather than an anchor around nothing. A URI in some *other* bucket also returns null: a
 * link to an object this stack never wrote would be a dead link presented as evidence.
 */
export function postmortemUrl(message: string): PostmortemLink | null {
  const match = S3_URI_PATTERN.exec(message)
  if (match?.index === undefined) return null
  const [uri, key] = match
  return {
    uri,
    key,
    href: postmortemHref(key),
    before: message.slice(0, match.index),
    after: message.slice(match.index + uri.length),
  }
}
