/** Tiny file-only debug log; never writes to the terminal. */

import { appendFileSync, mkdirSync } from "node:fs"
import { dirname, join } from "node:path"
import { homedir } from "node:os"

function debugLogPath(): string {
  const stateHome = process.env.XDG_STATE_HOME || join(homedir(), ".local", "state")
  return process.env.GEMMA_TUI_DEBUG_LOG || join(stateHome, "gemma", "tui-debug.log")
}

/** Append one debug line. Failures are ignored — logging must never break the UI. */
export function debugLog(message: string): void {
  try {
    const target = debugLogPath()
    mkdirSync(dirname(target), { recursive: true })
    appendFileSync(target, `${new Date().toISOString()} ${message}\n`, "utf8")
  } catch {
    // Best effort only.
  }
}
