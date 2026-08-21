/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        lib/redaction.ts
 * Purpose:          The one place the `[REDACTED: type]` token format is written down on the
 *                   frontend.
 * Interacts With:   components/LogSanitizerView.tsx
 *
 * Curriculum Project: Project 4 — Security, PII Redaction & Guardrails
 * Skills:           Contract Alignment, Drift Prevention
 * Tools:            TypeScript
 *
 * The token vocabulary is owned by `packages/contracts/.../security.py` (`RedactionType` and
 * `redaction_token`), and the six type names below must match its enum. They are not in
 * `contracts.gen.ts` because the generator exports enums the frontend *branches* on, and this is a
 * text format rather than a decision — but that makes it exactly the kind of thing that drifts, so
 * `tests/fixtures.ts` and `tests/unit/LogSanitizerView.test.tsx` build their masked lines through
 * `redactionToken`, so a name that drifts out of this list fails there.
 */

/** The six masked entity kinds, matching `RedactionType` in the contracts package. */
export const REDACTION_TYPES = ['password', 'ip', 'hostname', 'jwt', 'email', 'aws_key'] as const

export type RedactionKind = (typeof REDACTION_TYPES)[number]

/**
 * Matches any redaction token. Source form rather than a `RegExp` instance because the callers
 * need the `g` flag and `lastIndex` on a shared instance is a stateful trap.
 */
export const REDACTION_TOKEN_PATTERN = `\\[REDACTED: (?:${REDACTION_TYPES.join('|')})\\]`

/** Renders the token for a kind, mirroring `redaction_token()` on the Python side. */
export function redactionToken(kind: RedactionKind): string {
  return `[REDACTED: ${kind}]`
}
