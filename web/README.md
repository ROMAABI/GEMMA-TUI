# Gemma Web

A web-native frontend for the local Gemma workspace — OpenCode-inspired floating
cards, two-column dark layout, no terminal UI required.

```
┌──────────────┬─────────────────────────────────────────────────────────┐
│  sidebar     │  model header                                           │
│  new chat    ├─────────────────────────────────────────────────────────┤
│  recent      │  chat feed (floating message cards)                     │
│  accent      ├─────────────────────────────────────────────────────────┤
│              │  composer + live metrics                                │
└──────────────┴─────────────────────────────────────────────────────────┘
```

## Stack

- React 19 + TypeScript (strict)
- Vite 6
- Tailwind CSS 3.4 (no CDN fonts or external requests)
- `react-markdown` + `remark-gfm` for assistant replies
- `lucide-react` icons

## Run

```bash
cd web
npm install
npm run dev        # http://localhost:5173
```

Production build:

```bash
npm run build      # tsc --noEmit && vite build
npm run preview    # http://localhost:4173
```

## Paired server mode (recommended)

The web UI ships inside the Gemma TUI server, the same way OpenCode serves its
web app from the process that powers the terminal interface:

```bash
gemma-local          # in the TUI: /web or Ctrl+W
```

The TUI starts a local server on `127.0.0.1:47317`; `/web` (or `Ctrl+W`) mints a
one-time pairing link and opens it in your browser. The link expires after five
minutes and only works once; it sets an `HttpOnly` session cookie (`gemma_web`,
30 days) and redirects to the app. Run `/web` again for a fresh link. Headless/CLI
use:

```bash
gemma-local web --port 47317 --no-open   # prints "Pair: http://…/pair?token=…"
```

Because the browser and the terminal share one `SessionStore`, a chat started in
either place appears in both. The top bar shows **Paired**, the model is locked
to the model the server has loaded, Agent mode stays in the TUI (the toggle is
disabled), and messages stream from `POST /api/chat` (SSE) through
`core/webui.py`. The server stops when the TUI exits.

## Backend modes

By default the app runs in **demo mode**: replies are streamed locally by a mock
streamer, so the UI works with no model server. Sessions, model choice, mode and
accent persist in `localStorage` under `gemma-web:v1`.

`npm run dev` always uses demo mode: Vite has no `/api` routes, so the server
probe is skipped. To iterate on the paired UI against a dev server, set
`VITE_GEMMA_SERVER=1` and proxy `/api` to a running `gemma-local web`.

To talk to a real llama.cpp server directly from the dev server:

```bash
VITE_GEMMA_API_URL=http://127.0.0.1:8080 npm run dev
```

The client then uses `POST /v1/chat/completions` with `stream: true` and parses
the SSE `data:` frames, and probes `GET /health` for the connection badge. The
server must allow CORS from the dev origin (llama.cpp needs
`--host 0.0.0.0` plus a CORS-enabled build/proxy; otherwise put Vite's proxy in
front of it).

## Commands

| Command | Effect |
| --- | --- |
| `/new` | Start a new chat |
| `/clear` | Clear the visible chat |
| `/model [name]` | Show the model list or switch model |
| `/mode` | Toggle Chat / Agent |
| `/theme [accent]` | Cycle or set the accent (`cyan`, `violet`, `emerald`, `amber`) |
| `/rename <title>` | Rename the current chat |
| `/delete` | Delete the current chat |
| `/help` | List every command |

Slash commands autocomplete above the input: `↑`/`↓` to move, `Tab` or `Enter`
to complete, `Esc` to close.

## Keyboard

- `Enter` send, `Shift+Enter` newline
- `Ctrl/⌘ + N` new chat
- `Esc` close the mobile drawer

## Layout

- **Sidebar** (`w-[272px]`): brand, New Chat, search, scrollable recent chats
  with relative timestamps and an active accent bar, accent swatches.
- **Workspace**: model picker + connection badge + Chat/Agent toggle + clock;
  scrollable feed of message cards with author, timestamp, elapsed / first-token
  badge and copy buttons; composer with quick commands, slash palette, and a
  live metrics row (context, tok/s, elapsed, provider, temperature).
- Below `lg` the sidebar becomes a drawer. The workspace takes the full
  remaining width; the chat column is capped at `max-w-4xl` for readability.

## Source map

```
src/
  App.tsx                 layout + responsive drawer
  components/
    Sidebar.tsx           navigation, recent chats, accent picker
    TopBar.tsx            model picker, status, mode toggle, clock
    ChatFeed.tsx          scroll container, welcome state, auto-scroll
    MessageCard.tsx       user/assistant/note cards, copy, timing badges
    ToolCard.tsx          collapsible tool step card
    Composer.tsx          input, slash palette, metrics, send/stop
    Markdown.tsx          react-markdown wiring
    CodeBlock.tsx         fenced code with copy
  hooks/
    useChat.ts            sessions, streaming, commands, server mode
    useClock.ts           ticking local clock
    useConnection.ts      /health polling
    useCopy.ts            clipboard with fallback
  lib/
    api.ts                mock streamer + llama.cpp SSE adapter
    server.ts             /api/* client for the paired Gemma server
    format.ts             clock, relative time, token estimate
  data/seed.ts            demo sessions + welcome suggestions
  types.ts                Message / Session / Metrics / accents / models
```

## Verification

Checked in Chromium at 1500×950, 1100×900 and 700×900:

- new chat, welcome suggestions, send + streamed reply
- slash palette (8 commands, arrow/Tab completion)
- copy message and copy code block
- `/theme violet` updates the CSS accent variable
- model picker switches and persists across reload
- stop button marks the message `stopped`
- delete chat, session persistence via `localStorage`
- mobile drawer open/close, responsive sidebar
- real SSE adapter against a local streaming endpoint (no console errors)
