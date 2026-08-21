/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        lib/cn.ts
 * Purpose:          Merges Tailwind class strings so a caller's override wins over a
 *                   primitive's default instead of both landing in the class list.
 * Interacts With:   Every component, src/theme/tokens.ts
 *
 * Curriculum Project: Cross-cutting — Clean Code & Modular Ports
 * Skills:           Utility-First CSS, Class Composition
 * Tools:            clsx, tailwind-merge
 *
 * Without `twMerge`, `<Panel className="p-0">` would render `p-6 p-0` and the winner would
 * depend on stylesheet order rather than on the caller's intent.
 *
 * **The extension is load-bearing, not tidiness.** `tailwind-merge` resolves conflicts by grouping
 * classes on their prefix, and it only knows the *stock* Tailwind scales. Our type scale is custom,
 * so `text-log` (a size) and `text-console-output` (a colour) both looked like the same `text-`
 * group — and the merge kept only the last one. `cn('… text-log', 'text-console-output')` therefore
 * silently dropped the font size, and the retrieval console rendered at the inherited 16px beside
 * consoles at 12px. Nothing in the source looked wrong, which is what made it worth fixing here
 * rather than by avoiding the combination at each call site.
 */

import { clsx, type ClassValue } from 'clsx'
import { extendTailwindMerge } from 'tailwind-merge'

import { FONT_SIZE_NAMES } from '../theme/tokens'

/**
 * A merger that knows our type scale.
 *
 * Declaring the `font-size` group explicitly moves every custom size out of the `text-colour` group
 * it was being mistaken for. Colours need no declaration: anything `text-*` that is not named here
 * falls through to twMerge's own colour group, which accepts arbitrary values.
 */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [{ text: [...FONT_SIZE_NAMES] }],
    },
  },
})

/** Combines conditional classes and resolves Tailwind conflicts last-wins. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}
