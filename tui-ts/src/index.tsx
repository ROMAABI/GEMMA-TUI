/**
 * OpenTUI entry point for the Gemma terminal client.
 *
 * It pairs with the server (GEMMA_PAIR_URL) and renders the app.
 * Errors are shown full-screen so they're visible even when the UI fails to start.
 */

import { render } from "@opentui/solid"
import { createCliRenderer } from "@opentui/core"
import { GemmaClient, PairingError, serverAddress } from "./api"
import { createChat } from "./chat"
import { loadPrefs } from "./prefs"
import { App } from "./app"
import { COLORS, initTheme } from "./theme"

async function main() {
  const { base, pairUrl } = serverAddress()

  // Pair with the server (or reuse an existing cookie from the environment).
  let client: GemmaClient
  try {
    client = await GemmaClient.pair(base, pairUrl)
  } catch (error) {
    if (error instanceof PairingError) {
      renderError(error.message)
      process.exit(1)
    }
    throw error
  }

  // Create chat state machine.
  const prefs = loadPrefs()
  const chat = createChat(client)

  // Load server state.
  try {
    await chat.load()
    await chat.loadModels()
    await chat.loadProviders()
    await chat.loadCommands()
    // Default: a fresh New chat is selected by chat.load(). Only an explicit
    // session id or --continue reopens an existing conversation.
    const explicit = process.env.GEMMA_SESSION_ID
    const wantsContinue = process.env.GEMMA_CONTINUE === "1"
    if (explicit) {
      const session = chat.sessions().find((item) => item.id === explicit)
      if (session) chat.select(session.id)
    } else if (wantsContinue) {
      const last = chat.sessions().find((item) => item.messages.length > 0) ?? chat.sessions()[0]
      if (last) chat.select(last.id)
    }
  } catch (error) {
    renderError(error instanceof Error ? error.message : String(error))
    process.exit(1)
  }

  // Create the renderer first so the terminal palette can be read before the
  // first paint: `system` mode then starts on the right light/dark variant.
  const renderer = await createCliRenderer({})
  await initTheme(renderer, prefs.theme, prefs.themeMode)

  // Run the app. `render` returns once the renderer is attached; the app keeps
  // the process alive until it calls process.exit (see App's quit handler).
  await render(() => <App chat={chat} />, renderer)
}

function renderError(message: string) {
  render(() => (
    <box
      flexDirection="column"
      width="100%"
      height="100%"
      backgroundColor={COLORS.page}
      padding={4}
      alignItems="center"
      justifyContent="center"
    >
      <text style={{ fg: COLORS.danger }}>
        <b>gemma-tui error</b>
      </text>
      <text style={{ fg: COLORS.text }} marginTop={1}>
        {message}
      </text>
      <text style={{ fg: COLORS.faint }} marginTop={2}>
        Press any key to exit
      </text>
    </box>
  ))
  // Wait for a keypress so the error is visible.
  const stdin = process.stdin
  stdin.setRawMode?.(true)
  stdin.resume()
  stdin.once("data", () => process.exit(1))
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})