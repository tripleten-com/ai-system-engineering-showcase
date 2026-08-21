/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        BrandLogo.tsx
 * Purpose:          The TripleTen wordmark in the showcase header.
 * Interacts With:   components/Header.tsx, tripleten-wordmark.svg
 *
 * Curriculum Project: Cross-cutting — Marketing Delivery
 * Skills:           Brand Application, Accessible Imagery
 * Tools:            React 18, Vite asset handling, Tailwind CSS
 *
 * `tripleten-wordmark.svg` is the official asset, taken verbatim from TripleTen's own
 * `/gen-assets/logo/` and not redrawn. That matters: this file previously set the wordmark as type
 * in the display face, because a hand-traced logo is subtly off-brand in a way that goes unnoticed
 * precisely because it looks close. With the real vector available there is no reason to approximate
 * it, and no reason to touch its paths either — its `#1A1A1A` fills already match the `ink` token.
 *
 * Rendered through `<img>` rather than pasted into the JSX. It stays a file, so replacing the mark
 * means dropping in a new `.svg` without going near a component — and Vite folds it into the bundle
 * as a `data:` URI anyway (2.7KB, under the 4KB inline threshold), so this costs no extra request.
 * Inlining the markup would only buy recolouring through `currentColor`, and a brand mark is exactly
 * the thing that should *not* inherit whatever colour its container happens to have.
 *
 * Sized by height alone. The mark's 87.546:20 ratio does the rest, and pinning both axes is how a
 * logo ends up stretched.
 */

import wordmark from './tripleten-wordmark.svg'
import { cn } from '../lib/cn'

/**
 * The wordmark.
 *
 * `alt` is the brand name on its own — `Header.tsx` adds what the page *is* alongside it, so the
 * `h1` reads "TripleTen — Incident War Room" to a screen reader. Repeating "logo" in the alt text
 * would only make an assistive-technology user hear the word twice.
 */
export function BrandLogo({ className }: { className?: string }) {
  return (
    <img
      src={wordmark}
      alt="TripleTen"
      data-testid="brand-logo"
      className={cn('h-brand-logo w-auto select-none', className)}
    />
  )
}
