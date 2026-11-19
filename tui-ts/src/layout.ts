/**
 * Shared shell layout constants. Sidebar sizing has one source of truth so the
 * main area, composer and top bar stay in sync when the terminal is resized.
 */
/** Hide the rail below this terminal width (≈60% of a 200-col screen). */
export const SIDEBAR_HIDE_BELOW = 120

export const LAYOUT = {
  sidebar: {
    /** Minimum terminal width for each sidebar size. */
    breakpoints: { wide: 120, medium: 100 } as const,
    /** Sidebar columns at each breakpoint (below medium keeps the classic rail). */
    columns: { wide: 36, medium: 32, narrow: 28 } as const,
    /** Gap between the rail and the main area. */
    gutter: 1,
    /** Rail padding on each side, and padding inside a session row. */
    padding: 1,
    rowPadding: 1,
    /** Widest relative-time label ("Sep 14") in a session row. */
    timeColumn: 6,
    /** Fixed rows above/below the scrolling session list. */
    chromeRows: 13,
    minListRows: 3,
  },
  /** Pill (3 rows) + hint row and its margin: scroll content clears the composer. */
  composer: {
    reserveRows: 5,
  },
} as const

/** Sidebar width for the given terminal width — the one sizing constant. */
export const SIDEBAR_WIDTH = (terminalWidth: number): number => {
  const { breakpoints, columns } = LAYOUT.sidebar
  if (terminalWidth >= breakpoints.wide) return columns.wide
  if (terminalWidth >= breakpoints.medium) return columns.medium
  return columns.narrow
}

/** Space available for a session title before the timestamp column. */
export const sessionTitleWidth = (terminalWidth: number): number => {
  const { padding, rowPadding, timeColumn } = LAYOUT.sidebar
  return SIDEBAR_WIDTH(terminalWidth) - 2 * (padding + rowPadding) - timeColumn
}
