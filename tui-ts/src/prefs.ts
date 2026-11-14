/** Tiny preferences file for the terminal client (theme, mode, last session). */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { homedir } from "node:os"
import { isThemeMode, type ThemeMode } from "./theme"

export interface Prefs {
  /** Theme name (`opencode`, `violet`, …, `system`). */
  theme: string
  /** `system` follows the terminal's light/dark appearance. */
  themeMode: ThemeMode
  sessionId?: string
}

function prefsPath(): string {
  const configHome = process.env.XDG_CONFIG_HOME || join(homedir(), ".config")
  return join(configHome, "gemma-local", "tui-ts.json")
}

export function loadPrefs(): Prefs {
  try {
    const raw = readFileSync(prefsPath(), "utf8")
    const parsed = JSON.parse(raw) as Partial<Prefs> & { accent?: string }
    // `accent` is the pre-theme-mode key; keep reading it for old configs.
    const theme =
      typeof parsed.theme === "string"
        ? parsed.theme
        : typeof parsed.accent === "string"
          ? parsed.accent
          : "opencode"
    return {
      theme,
      themeMode: isThemeMode(parsed.themeMode) ? parsed.themeMode : "system",
      sessionId: parsed.sessionId,
    }
  } catch {
    return { theme: "opencode", themeMode: "system" }
  }
}

export function savePrefs(prefs: Prefs): void {
  try {
    const path = prefsPath()
    if (!existsSync(dirname(path))) mkdirSync(dirname(path), { recursive: true })
    writeFileSync(path, JSON.stringify(prefs, null, 2), "utf8")
  } catch {
    // Preferences are optional; never fail the TUI over them.
  }
}
