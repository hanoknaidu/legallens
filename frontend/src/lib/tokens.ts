/**
 * Reading design tokens from JavaScript.
 *
 * The `@theme` block in `styles/globals.css` is the single source of truth. Tailwind v4
 * emits every entry in it as a real CSS custom property on `:root` *and* as a utility
 * class, so there are two ways to consume a token and only one place to change it:
 *
 *   1. In markup — use the utility (`bg-cite-bg`, `text-ink-muted`, `w-panel`). Always
 *      prefer this. It is checked at build time and it costs nothing at runtime.
 *   2. In JavaScript — only where a value cannot be a class: a canvas fill, an inline
 *      SVG attribute, a computed pixel measurement. Use `cssVar()` below, which reads
 *      the live value rather than a copy of it.
 *
 * This file used to hold a hand-maintained table of hex literals mirroring the CSS. That
 * is a second source of truth, and a second source of truth for colour drifts silently —
 * nothing fails when it goes stale, the UI just stops matching itself. Reading the
 * computed property instead means a palette swap (or a dark theme, which redefines the
 * same names under a selector) needs no change here at all.
 */

/** Values used when there is no stylesheet to read — jsdom under test, SSR, early paint. */
const FALLBACK: Readonly<Record<string, string>> = {
  "--color-ink": "#121926",
  "--color-surface": "#ffffff",
  "--color-brand-500": "#3465a8",
  "--color-cite-bg": "#e4ecf8",
  "--color-cite-bg-active": "#c6d9f1",
  "--color-cite-line": "#3465a8",
};

/**
 * Read a CSS custom property off the document root.
 *
 * Not memoised: a cache would survive a theme change and hand back the previous
 * palette. `getComputedStyle` on `:root` is cheap, and these calls are rare by design —
 * if a token is being read on every frame, it belongs in a class or a CSS variable on
 * the element, not here.
 */
export function cssVar(name: `--${string}`, fallback?: string): string {
  if (typeof window === "undefined") return fallback ?? FALLBACK[name] ?? "";
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback || FALLBACK[name] || "";
}

/** Motion durations, mirrored from §3.3 of the design spec. Transitions themselves are
 *  CSS; these exist for the few places JS has to wait for one to finish. */
export const motion = {
  fast: 120,
  panel: 180,
  drawer: 240,
} as const;

/**
 * Layout values that live here rather than in CSS.
 *
 * The rule: a dimension consumed as a class belongs in `@theme` (the sidebar is
 * `w-sidebar`); a dimension computed at runtime belongs here. The document panel's width
 * is the result of pointer arithmetic clamped between these bounds, so CSS would only be
 * a second copy of them.
 */
export const layout = {
  panelMinPx: 400,
  panelMaxPx: 760,
  panelDefaultPx: 560,
} as const;
