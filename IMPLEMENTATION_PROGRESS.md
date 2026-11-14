# Implementation Progress

Live status of the OpenCode-inspired agent upgrade. Update after each verified
change. A feature is only "done" when its tests pass and (where relevant) it has
been exercised against a running llama.cpp server.

## Status Legend

- `[ ]` not started
- `[~]` in progress
- `[x]` implemented and tested
- `[!]` blocked / needs user input

## Phases

### 1. Provider abstraction and native tool calling
- [x] Provider event model
- [x] OpenAI-compatible streaming provider with `tools`
- [x] Prompt/XML fallback provider
- [x] Capability detection and graceful fallback

### 2. Tool framework
- [x] Tool spec/result/context types
- [x] Registry with aliases and OpenAI schemas
- [x] read / write / edit / glob / grep / list
- [x] bash with timeout and output truncation
- [x] multi-file patch
- [x] git status / diff / log / commit
- [x] webfetch (HTTP(S), HTML-to-text, size caps)

### 3. Permissions
- [x] Rule model and last-match evaluation
- [x] Default policy and config
- [x] TUI approval modal
- [x] Session "always allow" memory

### 4. Agent loop
- [x] Structured tool calls and tool messages
- [x] Concurrent tool execution with ordered results
- [x] Malformed JSON / unknown tool recovery
- [x] Context overflow detection and retry
- [x] Cancellation and timeout handling

### 5. Sessions
- [x] Schema migration (metadata, tool ids, summary, events)
- [x] Tool event persistence and replay
- [x] Compaction checkpoint persistence

### 6. Context compaction
- [x] Token estimation
- [x] Threshold trigger and manual `/compact`
- [x] Structured summary + recent window

### 7. Integrations
- [x] MCP stdio client and namespaced tools
- [x] LSP manager and diagnostic/hover/definition tools
- [x] Skills discovery and `skill` tool
- [x] Workspace `AGENTS.md` instruction loading

### 8. TUI
- [x] New event rendering and tool cards
- [x] Permission modal
- [x] Commands (`/permissions`, `/compact`, `/mcp`, `/skills`, `/lsp`, `/provider`, `/context`, `/init`)
- [x] Status bar agent state

### 9. Verification
- [x] Unit tests (109 passing)
- [x] Agent loop test with fake provider
- [x] Live llama.cpp native-tool smoke test
- [x] TUI launch smoke test

## Log

- 2026-11-06: Architecture research recorded in `docs/opencode-architecture.md`.
  Implementation started.
- 2026-11-06: Tool framework, permissions, providers, agent loop, sessions,
  compaction, MCP, LSP, and skills implemented; 44 tests passing.
- 2026-11-06: TUI headless smoke test added (`tests/test_tui.py`). Verified mount,
  focus, all new commands, and the permission modal (escape, allow, always).
- 2026-11-06: Settings save now merges with the existing TOML file instead of
  dropping unknown keys; added escaping and roundtrip tests (`tests/test_settings.py`).
- 2026-11-06: Added fake MCP and LSP servers and protocol tests
  (`tests/test_mcp.py`, `tests/test_lsp.py`). Both integrations pass end-to-end.
- 2026-11-06: Added git tool tests (`tests/test_git.py`); fixed clean-working-tree
  detection and the friendly outside-repo error.
- 2026-11-06: Added `webfetch` tool and tests (`core/tooling/web.py`,
  `tests/test_web.py`). Added `AGENTS.md` loading and tests (`tests/test_prompt.py`).
- 2026-11-06: Live llama.cpp validation: agent wrote `hello.py` via `write`,
  ran it via `bash`, and returned a verified summary. Session roles:
  `user/assistant/tool/assistant/tool/assistant`.
- 2026-11-06: Hardened the agent loop with a repeated-tool-call guard
  (warn at 3 identical rounds, stop at 4) and cancellation cleanup that
  persists cancelled tool results so no orphan `tool_calls` remain.
- 2026-11-06: Reworked the responsive footer to use the real footer width
  instead of a fixed padding estimate. Verified landing and chat views at
  160/120/100/80/60/50/40/35/30/24/20 columns; added `footer-nano` to hide
  the footer entirely below 24 cells. Added a regression test.
- 2026-11-06: Fixed false "Task completed." reports. The TUI no longer invents
  success when the agent returns no final text; the agent now tracks unresolved
  blocking tool failures, nudges the model once to correct a success claim, and
  annotates or summarizes honestly if it still does not. Added regression tests
  for permission-denied bash, workspace escapes, empty finals, and resolved
  failures.
- 2026-11-06: Live multi-task audit through the TUI (5 tasks, one session):
  directory+readback, bug fix+test, multi-file package, and denied-path
  honesty all verified. The single-file task exposed a real failure: the model
  created and verified `hello.py`, then overwrote it with the literal output
  and claimed success. Added a stale-verification guard that nudges the model
  to re-run the last successful check after a later mutation, annotates the
  final answer if it still claims success, and fixed local negation detection
  so "No further steps... task is complete" is recognized as a success claim.
- 2026-11-06: Diagnosed the "stuck at Step 1/10" report. The agent was not
  hung: the first native request has a ~2746 token prefix (1054 system prompt
  + 1692 tool schemas) and CPU prompt processing runs at ~37 tok/s, so the
  first token took ~73s. The streaming indicator only showed the newest
  reasoning chunk, so it looked frozen.
  - Indicator now accumulates reasoning/text and renders
    `Step N/M · <elapsed>s · <phase or text>` once per second, with explicit
    phases for initializing, warming up, waiting, reasoning, writing, tool,
    and analyzing.
  - Added a prompt-cache warmup: `AgentOrchestrator.warmup()` sends the exact
    system prompt plus tool schemas with `max_tokens=1` so llama.cpp caches
    the prefix. Measured on a guaranteed-cold prefix: warmup 68.9s, then the
    real step's first token in 1.8s (vs ~70s without it).
  - The TUI starts the warmup in the background once the server is healthy and
    gates the first real request on it (concurrent requests would compete for
    the same CPU slots). Disable with `[agent] warmup = false` or
    `GEMMA_WARMUP=0`.
  - Live check through the TUI: the same "create a directory" task dropped
    from 147s to 52.6s; the "warming up model" phase renders for the duration
    of the warmup and the first real request waits for it.
- 2026-11-11: Investigated "removing a directory took 10 steps". Reproduced
  two distinct bugs:
  - A corrected shell command did not clear the earlier failure. `rm dir`
    failed ("Is a directory"), `rm -r dir` succeeded, and the final answer was
    still prefixed with "Completion not verified" because failures were keyed
    by the exact command string. Failures are now also resolved by
    `(verb, target)` pairs, so `rm x` -> `rm -r x` counts as fixed while an
    unrelated later command (`ls`) does not.
  - Loops that never repeat the same call twice in a row were invisible. The
    repeat guard only counted consecutive identical calls, so `ls`, `test`,
    `ls`, `test` ran to the step limit. It now looks at a 6-step window and
    stops a two-call cycle.
  - Added a redundant-verification guard: after a successful change, 3
    read-only steps trigger a nudge to finish, and 5 stop the loop and
    summarize. Read-only exploration before any change is unaffected.
  - Added rule 10 to the system prompt: stop as soon as a tool result confirms
    the goal, and do not re-verify the same fact with different commands.
  - Live check: create dir then "remove the dir" now finishes in one step
    (14.6s) with a clean final answer instead of a false failure warning.
- 2026-11-11: Full suite: 109 passed.

- 2026-11-11: UI refactor — OpenCode-inspired floating tile layout.
  - Moved the structure-only stylesheet out of `BASE_CSS` into
    `tui/gemma_tui.css` (`CSS_PATH`). Per-theme colors still come from
    `tui/themes.py` (`CSS`) and win over the stylesheet because Textual reads
    `css_path` first, then `CSS`.
  - Floating rounded cards: sidebar is split into `#brand-card`, `#nav-card`,
    `#recents-card`, `#settings-card`; the workspace header is a card; chat
    bubbles, tool steps, code blocks and the prompt are all `border: round`.
    Focused prompt cards switch to the cyan accent via `:focus-within`.
  - Added the `opencode` theme (`#121212` screen, `#181818` panels, `#1a1a1a`
    cards, `#2a2a2a` borders, `#22d3ee` accent) and made it the default theme
    and `Settings.theme` default; user config set to `theme = "opencode"`.
  - An earlier 3-panel variant with a right context/MCP panel was reverted at
    the user's request; the layout stays at the original two panels
    (sidebar + workspace) with the new tile styling.
  - Header model name is fitted to the real widget width (`_update_model_label`,
    refit after resize) instead of clipping mid-word; the mode badge lost its
    round border, which was making it 3 rows tall inside the 1-row header and
    invisible.
  - Status bar drops trailing stats that do not fit instead of clipping.
  - Verified by screenshot at 150x46 (opencode, system, tokyo-night, dracula),
    96x40 and 70x30 (sidebar auto-hides), plus ctrl+b toggle, command palette
    and live theme switching.
  - Full suite: 109 passed.
- 2026-11-11: Sidebar widened from 30 to 40 columns (min 32, max 48) after
  feedback that it looked too narrow; the footer width estimate now reads the
  real sidebar width instead of a hard-coded 30. The sidebar also auto-hides
  below 104 columns (in addition to the existing 60%-of-screen rule) so the
  chat keeps a usable width on small terminals. Screenshots re-checked at
  150x46 and 96x40; suite still 109 passed.
- 2026-11-11: Clickable model/settings UI.
  - Clicking the model in the header (or `/model` in the footer) now opens a
    `ModelPicker` modal listing the active model, the model reported by
    `/v1/models`, and local `models/*.gguf` files. Selecting a row saves the
    model to the config and refreshes the header/status bar.
  - Clicking `⚙ Settings` now opens a `SettingsMenu` modal with Model, Theme,
    Mode (agent/chat), Permissions and Show config rows; the old `/config`
    dump is still reachable from there and via the command.
  - Fixed the ListView highlight selectors: component classes are `-highlight`
    (single dash), so every `.--highlight` rule was dead. Highlighted rows in
    the model picker, theme picker, command palette, autocomplete and session
    list now use the theme's card/accent colors instead of Textual's default
    blue.
  - Added 3 tests (header click opens picker, selection persists, settings
    menu lists model/theme). Full suite: 112 passed.
- 2026-11-11: Diagnosed "hello took 110.6s" in chat mode.
  - Reproduced on the live CPU server: a tiny chat request is ~9.3s idle
    (33-token prompt: 3.9s prompt processing + 5.3s for 12 tokens), and
    ~6.4s with a warm cache (1.4s first token).
  - Running the agent warmup (1912 prompt tokens + 13 tool schemas) at the
    same time made the same hello take 60.6s (prompt_ms 18.1s, predicted 6.3s
    while sharing the CPU). The warmup was started on every health check
    regardless of mode, and the chat path did not wait for it, so the first
    chat message raced the warmup. The extra web-search judge call
    (`decide_search`) added ~15s and, with this model, did not even return a
    usable YES/NO.
  - Fixes: warmup now only runs in agent mode (and starts on toggle to agent
    mode, is cancelled when switching back to chat); greetings skip the search
    judge via `_SMALLTALK_RE`; the assistant badge now shows
    `⚡ total · first token Xs`; tok/s uses generation-only time
    (`gen_time_active`/`last_gen_tokens`) instead of total wall time, so it no
    longer reports 0.1 tok/s for a cached prompt.
  - Added 2 tests (warmup mode gating, smalltalk regex). Suite: 114 passed.
- 2026-11-11: Added the web frontend (`web/`), replacing the terminal UI with a
  React 19 + TypeScript + Tailwind 3.4 app.
  - Layout: `#121212` page, `#181818` panels, floating `#1a1a1a` cards with
    `rounded-xl border border-[#2a2a2a]`; 3 columns at `xl` (sidebar, workspace,
    right rail), sidebar drawer below `lg`, rail hidden below `xl`.
  - Sidebar: New Chat, search, recent chats with relative time, active accent
    bar, delete-on-hover, accent swatches. Workspace: model picker, connection
    badge, Chat/Agent toggle, clock, message cards with author/timestamp/
    elapsed/first-token badges and copy, tool cards, composer with slash
    autocomplete and a live metrics row. Right rail: context ring, active
    tools, session info, shortcuts.
  - State: `useChat` owns sessions, streaming, commands and debounced
    `localStorage` persistence (`gemma-web:v1`); `useClock` and `useConnection`
    drive the header. Accent is a CSS variable switched by `/theme` or the
    swatches.
  - Backend: demo mock streamer by default; `VITE_GEMMA_API_URL` switches to the
    llama.cpp SSE adapter (`/v1/chat/completions`, `/health`).
  - Verified in Chromium with Playwright at 1500x950, 1100x900, 700x900:
    new chat, slash palette, send/stream, copy, `/theme`, model picker,
    stop, delete, reload persistence, drawer, and the real SSE adapter against a
    local streaming endpoint. `npm run build` (tsc + vite) passes; no console
    errors. No commit or push made.
- 2026-11-11: Removed the web right rail at the user's request. The workspace
  now takes the full width (sidebar + workspace only), the chat column is capped
  at `max-w-4xl`, and `RightRail.tsx` was deleted. Context/tok-s/elapsed/provider/
  temperature remain in the composer metrics row, and tool calls remain in the
  message cards. Rebuilt (`tsc` + vite) and re-ran the Playwright checks: all
  interactions pass, the rail is absent at every width, no console errors.
- 2026-11-11: Integrated the web frontend into the TUI the way OpenCode does —
  one local server serves the browser app and shares the TUI's state, gated by a
  one-time pairing link.
  - `core/webui.py`: `GemmaWebServer` (stdlib `ThreadingHTTPServer`, daemon
    thread) serves `web/dist` with SPA fallback and immutable asset caching, plus
    `GET /health`, `GET /api/info`, `GET/POST /api/sessions`,
    `GET/PATCH/DELETE /api/sessions/<id>`, `POST /api/sessions/<id>/clear` and
    `POST /api/chat` (SSE `token`/`done`/`error` frames). Chat runs chat-mode
    only through `LlamaClient` and persists both turns in the shared
    `SessionStore`. Timestamps are emitted as UTC ISO-8601 (`_iso`).
  - Pairing: `PAIR_TTL = 300`, single-use token compared with
    `secrets.compare_digest`, redirect sets an `HttpOnly; SameSite=Lax` cookie
    (`gemma_web`, 30 days). Unpaired API calls get 401, unpaired pages get a
    "Pair this browser" page; expired links get a dedicated page.
  - TUI: `ensure_web_server()` starts the server on `127.0.0.1:47317` (ephemeral
    when headless) with the TUI's own store, `/web` and `Ctrl+W` mint a fresh
    link and open the browser, `on_unmount` stops the server. `/web` documented
    in `COMMAND_DEFS` and `HELP_TEXT`. New CLI command `gemma-local web`
    (`--port`, `--host`, `--open/--no-open`) prints the pairing URL.
  - Web app: `src/lib/server.ts` (`/api/*` client + SSE parser) and `useChat`
    server-mode detection via `/api/info` (JSON content-type, `app === "gemma"`),
    shared sessions, server streaming, CRUD, provider label from the server,
    `Paired` badge, locked model picker, disabled Agent toggle, and a boot gate so
    demo chats never flash before the shared ones. Demo/`localStorage` mode is
    unchanged and skipped server detection in `npm run dev`.
  - Fixed a real bug found end-to-end: the SSE response declared
    `Connection: keep-alive` with no `Content-Length`, so the browser never saw
    the stream end and the composer stayed on "Stop"; it now sends
    `Connection: close` and sets `close_connection`.
  - Tests: `tests/test_webui.py` (pairing single-use, session CRUD, chat
    streaming + persistence, static/SPA, missing build) and two TUI tests
    (`/web` starts the shared server and mints fresh links; browser and TUI see
    each other's sessions). Suite: 122 passed.
  - Verified end-to-end in Chromium against `gemma-local web` + a fake
    llama.cpp: pairing sets the cookie, `Paired`/`llama.cpp` render, Agent and
    the model picker are disabled, a message streams "Hello from llama.cpp.",
    the reply and session survive a reload, the sidebar titles the chat, stop
    aborts and marks the message, a reused link returns 403, SQLite holds the
    user/assistant rows, and the console is clean. `npm run build` passes. No
    commit or push made.

- 2026-11-11: Replaced the Textual TUI as the default with an OpenTUI terminal
  client that consumes the same server as the web UI (`tui-ts/`).
  - `tui-ts/` (Bun 1.4.3, Solid, `@opentui/core`/`@opentui/solid` 0.8.4) pairs
    with `GEMMA_PAIR_URL`, then uses `/api/info`, `/api/sessions*`,
    `/api/models`, `/api/commands`, `/api/pair` and the SSE `/api/chat` stream.
  - UI: bordered header with the live model chip, sidebar (sessions, new chat,
    accent swatches), message feed with markdown (headings, lists, quotes, code
    blocks via `<code>` + `SyntaxStyle`), streaming tail-follow, composer with
    slash palette (Tab completion), metrics row, and model/help/rename overlays.
    Keys: Enter send, Shift+Enter newline, Ctrl+P palette, Ctrl+B sidebar,
    Ctrl+N new chat, Ctrl+L clear, Ctrl+T accent, Ctrl+K model, Ctrl+Y copy
    (OSC 52), Ctrl+W pair a browser, Ctrl+C stop, Ctrl+D/Q quit, F1 help.
  - `app/cli.py`: `gemma` starts `GemmaWebServer`, passes `GEMMA_URL`,
    `GEMMA_PAIR_URL` and `GEMMA_SESSION_ID`, then runs the OpenTUI client; stops
    the server on exit. `--classic` keeps the Textual TUI (agent mode,
    permissions, MCP). Missing Bun/`tui-ts` install, or a client that crashes in
    under two seconds, falls back to the Textual TUI with a hint.
  - Server: added `GET /api/models`, `POST /api/models`, `GET /api/commands` and
    `POST /api/pair` (fresh single-use link) in `core/webui.py`.
  - Tests: `tui-ts/src/app.test.tsx` drives the real renderer against a Bun mock
    backend (`testRender` + `captureCharFrame` + `mockInput`): shell render,
    streaming reply, slash palette, Tab completion, model picker, F1 help,
    sidebar toggle, new chat. `tests/test_cli_tui.py` covers default/classic
    routing, fallback and session forwarding. Suites: `bun test src` 17 passed,
    `pytest -q` 132 passed.
  - Only stop llama.cpp on exit when this launch actually started it, so a
    server that was already running survives the TUI (regression fixed after the
    default launcher started exercising the old `atexit` hook).
  - Notes: terminals cannot distinguish Ctrl+M from Enter (and Ctrl+H from
    Backspace), so the model picker uses Ctrl+K; F1 arrives as a real escape
    sequence. Verified in a pty against a fake llama.cpp: the client pairs,
    streams a markdown reply with a fenced code block, updates metrics, and
    quits cleanly with Ctrl+Q. No commit or push made.

- 2026-11-11: Restored the classic Textual layout in the OpenTUI client while
  keeping the web-UI palette and components.
  - `tui-ts/src/app.tsx` is now a shell with the sidebar on the left and a
    workspace on the right, matching `tui/app.py`'s `compose()`.
  - New `components/topbar.tsx`: `☰` toggle, `Model: … ˅` (opens the picker),
    mode badge, `● CONNECTED`/`● OFFLINE`, clock, `?` help. The clock and long
    model names drop out as the terminal narrows.
  - `components/sidebar.tsx`: nested cards for the brand, NAVIGATION (`+ New
    Chat`, `💬 Chats`), RECENT CHATS (scroll list with active marker) and
    Settings (accent swatches). Collapses automatically below 104 columns;
    Ctrl+B overrides until the next threshold crossing. Settings is hidden on
    terminals shorter than 32 rows so the chat list keeps its rows.
  - New `components/landing.tsx`: centered brand, three suggestion cards that
    fill the prompt with their prefix, and the composer below — the landing
    screen returns whenever the active chat has no messages (`/new`, `/clear`).
  - `components/message.tsx`: rounded cards with `❯`/`◆` avatars, name, time,
    `❐ Copy` (OSC 52) and the `⚡ elapsed · first token · tok/s` badge.
  - `components/composer.tsx`: 4-row input card with a filled `✈ Send` (or
    `■ Stop`), slash palette above the card in chat and below it on the landing
    screen, and the `/new /mode /model /theme /rename /delete /clear /help`
    chips (they thin out when the terminal is narrow).
  - New `components/statusbar.tsx`: `context · tok/s · time · provider · temp`
    with the least important parts dropped first, like the classic status bar.
  - `components/markdown.tsx`: fenced code is a rounded card with a language
    header and its own `❐ Copy`.
  - Tests: `bun test src` 19 passed (adds landing render, suggestion click,
    Ctrl+P in chat, and asserts the landing hides after sending).
  - Verified in a real pty (pyte) against the fake llama.cpp: landing, sidebar
    cards, top bar, suggestion click, palette + Tab, model picker, F1 help,
    sidebar toggle, streamed markdown reply with a code card, Ctrl+Y copy,
    Ctrl+W pairing link, Ctrl+Q exit 0. No commit or push made.

- 2026-11-13: Turned `gemma` into an OpenCode clone: OpenCode layout and keys
  on top of the shared server, plus the full agent runtime exposed over HTTP.
  - New `core/provider_catalog.py`: the local llama.cpp server is the default
    provider and always first (`is_default: true`); extra OpenAI-compatible
    endpoints come from `GEMMA_PROVIDERS` (JSON array of
    `{id,name,base_url,models}`) and are probed via `/v1/models` when no models
    are declared. `switch()` points `base_url` + `model` at a provider and saves.
  - `config/settings.py`: `save_endpoint(base_url, model)` writes both in one go.
  - New `core/agent_service.py`: `AgentRuntime` runs the agent on one dedicated
    asyncio loop (MCP/LSP clients are loop-bound) and streams events through a
    thread-safe queue; `PermissionBroker`-style approval emits
    `permission_request` into the stream and awaits
    `POST /api/permissions/<id>`; runs can be cancelled with
    `POST /api/agent/abort`. The tool registry (filesystem, shell, patch, git,
    web, skills, LSP, MCP) is built once and cached; the orchestrator gets
    `store` + `session_id`, so assistant/tool turns persist as usual.
  - `core/webui.py`: new `GET/POST /api/providers`, `POST /api/mode`,
    `POST /api/agent` (SSE: step/text/reasoning/tool/usage/permission/complete),
    `POST /api/agent/abort`, `POST /api/permissions/<id>`; `POST /api/models`
    accepts `{provider, name}`; `/api/info` reports the active provider,
    endpoint, permission mode and `provider_mode`; `/api/sessions/<id>` now
    returns `tool_call_id`/`name` so tool history rehydrates in the client.
  - `tui-ts` rewritten to the OpenCode screen: borderless dark canvas, 30-column
    session rail (`+ new session`, SESSIONS, workspace/mode footer), slim
    transcript header, tinted user blocks with an accent rail, plain assistant
    replies with reasoning and inline tool blocks (`▸/✓/✗ name args` + result
    preview), and a bordered `❯ Ask anything…` prompt with an agent/chat chip,
    model and cwd row. New overlays: provider-grouped model picker, theme
    picker, permission dialog (y/a/n), help, rename.
  - Keymap: Tab switches chat/agent in the prompt (slash palette still owns Tab
    for completion), Ctrl+X leader (`n l m t a b h c y w q`), Ctrl+K models,
    Ctrl+T theme, Ctrl+P palette, Ctrl+B sidebar, Ctrl+Y copy, Ctrl+W pairing,
    F1/Ctrl+H help, Ctrl+C stop, Ctrl+D/Q quit.
  - `chat.ts` messages now carry ordered `parts` (text/reasoning/tool); agent
    events map onto them and the turn is reconciled from the server afterwards.
  - Tests: `pytest -q` 137 passed (provider catalog/default ordering,
    `/api/providers`, provider-aware model switch, `/api/mode`, agent SSE with a
    fake orchestrator, permission round-trip, unknown permission/abort);
    `bun test src` 23 passed and `tsc --noEmit` clean (adds agent tool +
    permission flow, provider picker, theme picker, Tab mode switch, Ctrl+X
    leader). Verified in a pty (pyte) against a fake llama.cpp that emits a real
    tool call: landing, permission dialog, approval, tool block, file written by
    the agent, leader help, provider picker, Tab mode switch, sidebar toggle and
    Ctrl+Q exit 0. No commit or push made.

- 2026-11-13: Gave the OpenTUI client OpenCode's theme model — a theme *name*
  plus a `system | dark | light` *mode*, with a terminal-derived `system` theme.
  - `tui-ts/src/theme.ts` rewritten: full `Palette` tokens (page, panel, card,
    element, borders, text ramp, code, danger/success/warning/info) with a dark
    and a light variant per theme (`opencode`, `violet`, `cyan`, `emerald`),
    plus `system`. `resolveTheme(name, mode, terminal)` picks the variant;
    `system` mode follows the detected terminal appearance.
  - The `system` theme is built from the terminal: background/foreground from
    OSC 10/11, accent + feedback colors from the ANSI palette (OSC 4), surfaces
    blended from background toward foreground. Without a palette it falls back
    to `RGBA.defaultBackground()`/`defaultForeground()` and ANSI slots, so it
    still inherits the terminal instead of forcing gemma's colors.
  - `initTheme(renderer)` (called from `index.tsx` before the first paint) reads
    `renderer.getPalette()` and subscribes to `theme_mode`, so a terminal that
    flips light/dark repaints the UI live.
  - Components now read the live palette via `colors()` instead of the old
    module-level `COLORS` constant; `COLORS` remains as the static dark
    fallback for the startup error screen. Theme state moved out of `chat.ts`
    into `theme.ts` (`theme`, `setTheme`, `setThemeMode`, `cycleThemeMode`).
  - Theme picker: mode row (`system`/`dark`/`light`, `←→` or `m` to cycle) above
    the theme list, per-row swatches from the resolved palette, and
    `terminal · light|dark` on the System row. `/theme <name>` selects a theme;
    `/theme dark|light|system` sets the mode.
  - Prefs: `{"theme", "themeMode"}` in `~/.config/gemma-local/tui-ts.json`; the
    old `accent` key is still read. Defaults are `opencode` + `system` (same
    shape as OpenCode's `theme.name` + `theme.mode`).
  - Tests: `bun test src` 33 passed (new `theme.test.ts`: variants, system
    palette derivation, fallback, mode inference, renderer normalization; new
    UI tests for the picker mode row, mode cycling, selecting System and
    `/theme` commands) and `tsc --noEmit` clean; `pytest -q` 137 passed.
  - Verified in a pty (pyte) with a mock server and a scripted terminal palette:
    the system theme painted the terminal background/foreground/accent, `system`
    mode resolved to the light variant, `/theme dark` and `/theme light`
    repainted, the picker showed the mode row and `terminal · light`, and a live
    dark-mode report (OSC 997) repainted the app dark. No commit or push made.

- 2026-11-13: Rebuilt the OpenTUI home screen as an OpenCode landing clone,
  branded `gemma`, and made it the default start screen.
  - `tui-ts/src/components/landing.tsx` rewritten: full-bleed slate canvas
    (`#504e55`), top-left session tab (`⋮ <title>` + `+ New session`), centred
    3-row pixel wordmark (`gem` light grey, `ma` white, OpenCode `_`/`^`
    shading as tinted half blocks), `/update to install v<version>` hint,
    the prompt (passed in as children) at `maxWidth` 80, and the footer
    (`⊙ N MCP /mcps` left, `v<version>` right). Narrow terminals fall back to
    a plain two-tone `gemma` wordmark.
  - `tui-ts/src/components/composer.tsx` gained a `landing` variant: dark box
    (`#2f2d33`) with a pink left rail (`#f5a3b8`), no prompt glyph or send
    badge, placeholder `Ask anything… "Fix broken tests"`, and an in-box status
    row `Build · Gemma 3 12B Local / Ollama · high` with right-aligned
    `shift+tab agents` / `ctrl+p commands`. The chat variant is unchanged and
    the landing variant still submits on Enter, opens the slash palette and
    toggles chat/agent with Tab.
  - `tui-ts/src/app.tsx`: the landing hides the sidebar, header and status bar
    and paints the root/main column with the landing canvas; `title`, `version`
    (from `/api/info`), MCP count (0 — the server exposes no MCP count yet) and
    `onNew` are passed down. All session/streaming logic is untouched.
  - `tui-ts/src/theme.ts`: added the fixed `LANDING` palette and exported the
    `tint` helper used for the wordmark shading. The landing deliberately does
    not follow the theme palette.
  - Tests: `bun test src` 33 passed (landing test now asserts the tab, hint,
    placeholder, status row, hints, MCP/version footer and that the rail is
    hidden; the New-session click and Ctrl+B tests were updated for the new
    start screen) and `tsc --noEmit` clean; `pytest -q` 137 passed.
  - Verified in a pty (pyte) against the mock server at 120x34 and 84x28: the
    wordmark rows and both colours, update hint, placeholder, pink left border,
    dark box, status row, key hints, tabs, MCP/version footer and slate
    background all matched. No commit or push made.

## 2026-11-13 (later) — Gemini-style home redesign (OpenTUI, UI only)

  - `tui-ts/src/components/landing.tsx` rewritten as a purely presentational
    Gemini home canvas: centred greeting (`Where should we start?`) over an
    exact 8-row bitmap wordmark (`gemma`, 26 columns, one empty column between
    letters) rendered as 4 rows of half blocks (`▀` fg=top pixel, bg=bottom
    pixel). `gem` is light grey `#c8c6cc`, `ma` white `#ffffff`, letter
    counters inked `#3a393e` (new `LANDING.hole`), empty pixels the `#504e55`
    canvas. Below 40 columns it falls back to plain two-tone text. The old
    session tab, `/update` hint and MCP/version footer moved out or dropped.
  - `tui-ts/src/components/composer.tsx` rewritten around one rounded pill used
    by both variants (renamed `home`/`chat`): `+` glyph, textarea (placeholder
    `Ask anything… (/ for commands)`), real `<model> ⌄` selector that opens the
    model picker, and a `➤`/`■` send/stop badge. Under the pill: real mode chip
    + `tab mode` (left) and real provider + `ctrl+k models` / `ctrl+b sidebar`
    (right, width-gated). The `metrics`/`workspace`/`status` props (with their
    fake `Build` / `Gemma 3 12B Local` / `Ollama` / `high` strings) are gone;
    `provider` and `onModel` were added. Palette, Enter-to-send and Tab
    completion logic are untouched.
  - `tui-ts/src/components/sidebar.tsx` (now 28 columns): `◆ gemma` + search
    glyph, `New chat` (highlighted, `ctrl+n`, wired to the real new-session
    flow), `Library`/`Scheduled`/`More` labels, real `Sessions` list with
    relative times, and a `username()` + `local · offline` + gear footer. The
    rail is tinted a step darker than the page (`tint(page, black)`, 0.5 dark /
    0.05 light) and now shows on the home screen too. `mode`/`workspace` props
    dropped.
  - `tui-ts/src/components/topbar.tsx`: slim transparent bar — session title
    left, model + clock right (☰ toggle, connection dot and the rule removed).
  - `tui-ts/src/components/statusbar.tsx`: transparent strip — real
    `ctx X/Y` (+ `tok/s` when streaming) left, `ctrl+p commands` + `v<version>`
    from `/api/info` right (version only ≥ 70 columns). No MCP/update text.
  - `tui-ts/src/app.tsx`: pill width is `min(main-4, max(46, 70% of main))`;
    the header, rail and status bar render in both states; the root paints
    `LANDING.background` on the home screen and the theme page in chat.
  - `tui-ts/src/theme.ts`: added `hole: "#3a393e"` to the fixed `LANDING`
    palette (nothing else changed).
  - Tests: `tsc --noEmit` clean; `bun test src` 33 passed (landing tests now
    assert the greeting, pill, real model/provider/mode data, the rail on the
    home screen and the absence of every fake string; the New-session click
    moved to the sidebar `ctrl+n` row); `pytest -q` 137 passed.
  - pty/pyte e2e against the mock server: `/tmp/opencode/e2e/landing_check.py`
    rewritten for 120x40 + 80x24 (wordmark geometry + colours, greeting, pill
    corners/`+`/`⌄`/`➤`, status row, top/bottom bars, rail items, slate canvas,
    rail `#050505`, no overflow, then send `hi` → chat view with `Hello`);
    `theme_check.py` re-pointed to the main-area slate cell. All 15 theme and
    both size runs pass. No commit or push made.

## 2026-11-15 — Home screen now follows the active theme (OpenTUI, UI only)

  - `tui-ts/src/theme.ts`: deleted the fixed `LANDING` palette entirely — the
    home screen now reads every colour from the live theme tokens, same as the
    chat view (`page` canvas, `card`/`border` pill, `muted` placeholder,
    `faint` hints, `element`/`accent` mode chip, `dim`/`text` wordmark,
    `panel` letter counters). No hardcoded hex remains in the landing files.
  - `tui-ts/src/components/landing.tsx`: the wordmark runs are grouped by theme
    token and resolved reactively (a `createMemo` over `colors()`), so
    `/theme` switches repaint the home live. Greeting uses `text`; the canvas
    uses `page`.
  - `tui-ts/src/components/composer.tsx`: dropped the `home`/`chat` variant
    split and the `Surface` helper — one pill using the chat composer tokens
    exactly (accent send badge + agent chip, `element` chat chip, `muted`
    placeholder, theme `card`/`border` box). `app.tsx` paints the root with
    `colors().page` in both states and no longer passes a variant.
  - Tests: `tsc --noEmit` clean; `bun test src` 33 passed; `pytest -q` 137
    passed.
  - pty/pyte e2e (`/tmp/opencode/e2e/landing_check.py`, recreated with the
    mock server after the workspace restart): home screenshot vs chat
    screenshot at 120x40 agree on all shared colour samples (pill bg `161616`,
    border `262626`, placeholder `7a7a7a`, badge accent `7fd88f` under
    emerald, chip `1e1e1e`/`a0a0a0`, hints/metrics `565656`); `/theme
    opencode|violet|emerald` each repaint the home badge live; the 80x24 run
    stays overflow-free on the theme canvas. `theme_check.py` updated to
    expect the landing canvas to follow the terminal background (system
    theme). All checks pass. No commit or push made.

## 2026-11-15 (later) — Sidebar nav cleanup (OpenTUI, UI only)

  - `tui-ts/src/components/sidebar.tsx`: removed the static `Library`,
    `Scheduled` and `More` nav items (labels only — they had no handlers or
    keybindings) and grew the session list to absorb the freed rows
    (`listHeight` now `height - 13`). Only `New chat` (ctrl+n, wired to the
    real new-session flow) and the `Sessions` list remain; the search glyph and
    the `username()` / `local · offline` / gear footer are untouched.
  - `tui-ts/src/app.test.tsx`: sidebar assertions updated (removed items now
    asserted absent).
  - Verification: `tsc --noEmit` clean; `bun test src` 33 passed; pty
    `landing_check.py` passes at 120x40 and 80x24 (120x40 confirms the rail
    shows only ◆ gemma/⌕, New chat ctrl+n, Sessions and the footer, and
    `theme_check.py` all 15 pass. No commit or push made.
