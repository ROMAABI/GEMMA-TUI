# Gemma OpenTUI client

Terminal client for the local Gemma server, built with Bun, Solid and
[OpenTUI](https://github.com/sst/opentui) (the same stack OpenCode V2 uses).
It is a thin client: the Python server in `../core/webui.py` owns sessions,
providers, the SSE chat stream and the agent runtime, and the browser UI talks
to the same routes.

## Run

`gemma` (or `gemma-local`) starts the server and this client for you:

```bash
gemma              # OpenTUI client (default)
gemma --classic    # Textual TUI
```

Standalone, against an already-running server:

```bash
bun install
GEMMA_URL=http://127.0.0.1:47317 GEMMA_PAIR_URL='http://127.0.0.1:47317/pair?token=…' \
  bun run src/index.tsx
```

`GEMMA_SERVER_URL` can override the base URL when `GEMMA_URL` is unset;
`GEMMA_SESSION_ID` opens a specific session.

## Develop

```bash
bun run typecheck   # tsc --noEmit
bun test src        # real renderer against a Bun mock backend
```

`bunfig.toml` preloads `@opentui/solid/preload` so JSX compiles for the terminal;
without it Solid resolves to its server build and rendering throws
`Orphan text error`.

## Layout

### Home screen

A Gemini-style home, branded `gemma`: a centred bitmap wordmark and one
rounded prompt pill on the theme canvas, with a darker rail. Every colour comes
from the active theme — the home and the chat view share the same tokens. The
rail, top bar and bottom bar are visible from the start:

```
   Theme check                                mock-gemma  10:04 PM
─ rail ─────────────────
│ ◆ gemma      ⌕  │            ▀▀▀▀▀▀ ▀▀▀▀ ▀▀▀▀▀ ▀▀▀▀▀ ▀▀▀▀   (4 rows of ▀)
│ New chat ctrl+n  │
│ Sessions         │           Where should we start?
│ │ First chat now │
│                  │        ╭─────────────────────────────────────────╮
│                  │        │ + Ask anything… (/ for commands)  ➤    │
│                  │        ╰─────────────────────────────────────────╯
│                  │         chat  tab mode      llama.cpp · ctrl+k models
│ Spix             │
│ local · offline ⚙│        ctx 0/8,192                     ctrl+p commands · v0.1.0
```

- The wordmark is an exact 8-row bitmap font drawn as 4 rows of half blocks:
  `gem` uses the theme `dim` text colour, `ma` the bright `text` colour, the
  letter counters the `panel` colour and the empty pixels the `page` canvas.
  Below 40 columns it falls back to a plain two-tone `gemma` text.
- The top bar shows the session title, the model and the clock; the bottom bar
  shows real context metrics, `ctrl+p commands` and the version from
  `/api/info`. There are no MCP/update decorations (the server exposes neither).
- The prompt pill is 70% of the main area (clamped 46..main-4) with real data
  only: the `<model> ⌄` selector opens the model picker (like Ctrl+K), the
  mode chip reflects the real chat/agent mode and `Tab` still toggles it.
- The rail is always a step darker than the page (`tint(page, black)`), shows
  the real sessions and collapses below 92 columns.
- The prompt pill reuses the chat composer tokens exactly (`card` box, `border`
  edge, `muted` placeholder, `accent` send badge and agent chip), so a theme
  switch repaints the home and the chat together.

### Chat

After the first message the greeting is replaced by the transcript and the pill
moves to the bottom:

```
│ rail │              First chat                 mock-gemma  10:04 PM
│ ◆ gemma      ⌕    │
│ New chat ctrl+n   │  │you  10:04
│ Sessions          │  │hi there
│ │ First chat now  │
│                   │  gemma  mock-gemma  10:04
│                   │  Hello from the mock
│                   │  ✓ write  notes.txt                   0.2s
│                   │    Wrote 12 lines
│ Spix              │
│ local · offline ⚙ │     ╭─────────────────────────────────────────╮
│                   │     │ + Ask anything… (/ for commands)  ➤     │
│                   │     ╰─────────────────────────────────────────╯
│                   │      chat  tab mode       llama.cpp · ctrl+k models
│                   │  ctx 1.2k/8,192 · 12.4 tok/s    ctrl+p commands · v0.1.0
```

### Themes

The client uses the same model as OpenCode: a **theme name** plus a **mode**.

- Names: `opencode` (default), `violet`, `cyan`, `emerald`, `system`.
- Modes: `system` (default, follows the terminal's light/dark appearance),
  `dark`, `light`.
- Every built-in theme ships a dark and a light palette, so `mode` switches the
  whole UI, not just the accent.
- The `system` theme builds its palette from the terminal itself: background and
  foreground come from OSC 10/11, the accent and feedback colors from the ANSI
  palette (OSC 4). It keeps following the terminal when its appearance changes.
  Terminals that do not answer the queries fall back to the terminal's default
  colors.
- `/theme` opens the picker (`↑↓` theme, `←→` or `m` mode, `Enter` apply);
  `/theme violet` selects a theme; `/theme dark`, `/theme light` and
  `/theme system` set the mode. `Ctrl+T` opens the picker too.
- Choices persist in `~/.config/gemma-local/tui-ts.json` as
  `{"theme": "...", "themeMode": "..."}`; the old `accent` key is still read.

| File | Role |
|---|---|
| `src/index.tsx` | pairing, state load, palette detection, `render()` |
| `src/app.tsx` | shell, keymap (incl. Ctrl+X leader), commands, overlays |
| `src/theme.ts` | palettes, `system` theme, mode resolution, live theme state |
| `src/prefs.ts` | theme/mode/last-session preferences |
| `src/chat.ts` | sessions, parts (text/reasoning/tool), streaming, permissions |
| `src/api.ts` | JSON + SSE client (`GemmaClient`) |
| `src/markdown.ts` | small block/inline markdown parser |
| `src/components/topbar.tsx` | top bar (session title, model, clock) |
| `src/components/sidebar.tsx` | Gemini rail: brand, nav, sessions, user footer |
| `src/components/landing.tsx` | Gemini home canvas: bitmap wordmark, greeting |
| `src/components/feed.tsx` | scrollable transcript |
| `src/components/message.tsx` | user/assistant blocks, reasoning, tool blocks |
| `src/components/composer.tsx` | prompt pill (home + chat variants), palette, status row |
| `src/components/statusbar.tsx` | context metrics, commands hint, version |
| `src/components/overlays.tsx` | model, theme, permission, help, rename |

OpenTUI's `<markdown>` renderable paints nothing without tree-sitter, so
assistant replies are parsed by `src/markdown.ts` and rendered as styled
`<text>` plus `<code>` blocks.

## Notes

- Terminal chords are ASCII control codes: Ctrl+M is Enter and Ctrl+H is
  Backspace, so the model picker is Ctrl+K and help is F1.
- `scrollbox` with `flexGrow` inside a fixed-height box swallows the preceding
  `<text>`; give it an explicit `height` instead.
- Styling inside text spans uses `style={{ fg, bg }}`; `span`/`b`/`i` have no
  direct `fg` prop.
- Agent state lives on one background asyncio loop in `core/agent_service.py`
  because MCP/LSP clients are bound to the loop that created them; the SSE
  handler only drains a thread-safe queue.
