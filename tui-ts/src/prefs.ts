/** Tiny preferences file for the terminal client (theme, mode, session, prompt). */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { homedir } from "node:os"
import { isThemeMode, type ThemeMode } from "./theme"

export interface Sampling {
  temperature: number
  top_p: number
  repeat_penalty: number
  /** Disable Qwen "thinking" mode for casual chat (llama.cpp chat-template flag). */
  no_think?: boolean
}

export const DEFAULT_SYSTEM_PROMPT = ""

export const DEFAULT_SAMPLING: Sampling = {
  temperature: 0.6,
  top_p: 0.9,
  repeat_penalty: 1.1,
  no_think: true,
}

export interface Prefs {
  /** Theme name (`opencode`, `violet`, …, `system`). */
  theme: string
  /** `system` follows the terminal's light/dark appearance. */
  themeMode: ThemeMode
  sessionId?: string
  /** System prompt prepended to every chat/agent turn. */
  systemPrompt?: string
  /** llama.cpp sampling defaults. */
  sampling?: Sampling
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
    const sampling = parsed.sampling
    return {
      theme,
      themeMode: isThemeMode(parsed.themeMode) ? parsed.themeMode : "system",
      sessionId: parsed.sessionId,
      systemPrompt:
        typeof parsed.systemPrompt === "string" ? parsed.systemPrompt : DEFAULT_SYSTEM_PROMPT,
      sampling: {
        no_think: sampling?.no_think ?? DEFAULT_SAMPLING.no_think,
        temperature:
          typeof sampling?.temperature === "number" && Number.isFinite(sampling.temperature)
            ? sampling.temperature
            : DEFAULT_SAMPLING.temperature,
        top_p:
          typeof sampling?.top_p === "number" && Number.isFinite(sampling.top_p)
            ? sampling.top_p
            : DEFAULT_SAMPLING.top_p,
        repeat_penalty:
          typeof sampling?.repeat_penalty === "number" && Number.isFinite(sampling.repeat_penalty)
            ? sampling.repeat_penalty
            : DEFAULT_SAMPLING.repeat_penalty,
      },
    }
  } catch {
    return {
      theme: "opencode",
      themeMode: "system",
      systemPrompt: DEFAULT_SYSTEM_PROMPT,
      sampling: DEFAULT_SAMPLING,
    }
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
