import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ACCENTS,
  CONTEXT_MAX,
  MODELS,
  type AccentPreset,
  type Message,
  type Metrics,
  type Mode,
  type Session,
  type ToolStep,
} from "../types";
import { seedSessions } from "../data/seed";
import { estimateTokens, titleFromPrompt } from "../lib/format";
import { hasLiveBackend, streamReply } from "../lib/api";
import {
  clearSession,
  createSession,
  deleteSession,
  detectServer,
  fetchSession,
  fetchSessions,
  renameSession,
  streamServerChat,
  type ServerInfo,
} from "../lib/server";

const STORAGE_KEY = "gemma-web:v1";

interface PersistedState {
  sessions: Session[];
  activeId: string;
  model: string;
  mode: Mode;
  accentId: string;
  userName: string;
}

const HELP_TEXT =
  "**Commands**\n\n" +
  "- `/new` — start a new chat\n" +
  "- `/clear` — clear the visible chat\n" +
  "- `/model [name]` — show or switch the active model\n" +
  "- `/mode` — toggle Chat / Agent\n" +
  "- `/theme [cyan|violet|emerald|amber]` — switch the accent\n" +
  "- `/rename <title>` — rename this chat\n" +
  "- `/delete` — delete this chat\n" +
  "- `/help` — show this help";

function makeId(prefix = "s"): string {
  const raw =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID().replace(/-/g, "")
      : Math.random().toString(36).slice(2).padEnd(12, "0");
  return `${prefix}${raw.slice(0, 12)}`;
}

function defaultState(): PersistedState {
  const sessions = seedSessions();
  return {
    sessions,
    activeId: sessions[0].id,
    model: MODELS[0],
    mode: "chat",
    accentId: "cyan",
    userName: "Spix",
  };
}

function loadState(): PersistedState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<PersistedState>;
      if (Array.isArray(parsed.sessions) && parsed.sessions.length > 0) {
        return {
          ...defaultState(),
          ...parsed,
          sessions: parsed.sessions,
        };
      }
    }
  } catch {
    // Corrupt storage: fall back to the seeded demo.
  }
  return defaultState();
}

function emptyMetrics(): Metrics {
  return {
    contextUsed: 0,
    contextMax: CONTEXT_MAX,
    tokens: 0,
    elapsed: 0,
    tokPerSec: 0,
    provider: "auto",
    temperature: 0.7,
  };
}

function mapDetail(detail: Awaited<ReturnType<typeof fetchSession>>, userName: string): Session {
  return {
    id: detail.id,
    title: detail.title || "New chat",
    createdAt: detail.messages[0]?.created_at ?? detail.updated_at,
    updatedAt: detail.updated_at,
    messages: detail.messages
      .filter((item) => item.role !== "system")
      .map((item, index) => ({
        id: `${detail.id}-m${index}`,
        role: item.role === "assistant" ? "assistant" : "user",
        author: item.role === "assistant" ? "Gemma" : userName,
        content: item.content,
        createdAt: item.created_at || detail.updated_at,
      })),
  };
}

export function useChat() {
  const [state, setState] = useState<PersistedState>(loadState);
  const [streaming, setStreaming] = useState(false);
  const [streamingId, setStreamingId] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<Metrics>(emptyMetrics);
  const [focusSignal, setFocusSignal] = useState(0);
  const [server, setServer] = useState<ServerInfo | null>(null);
  const [booting, setBooting] = useState(true);
  const abortRef = useRef<AbortController | null>(null);
  const streamingRef = useRef(false);
  const serverRef = useRef<ServerInfo | null>(null);

  // When the page is served by the Gemma server (the TUI's /web), use the
  // shared session store instead of localStorage demo data.
  useEffect(() => {
    // Vite's dev server has no /api route, so probing there only logs a 404.
    // Set VITE_GEMMA_SERVER=1 when iterating on the paired UI against a proxy.
    if (import.meta.env.DEV && import.meta.env.VITE_GEMMA_SERVER !== "1") {
      setBooting(false);
      return;
    }
    let cancelled = false;
    void (async () => {
      const info = await detectServer();
      if (cancelled || !info) return;
      try {
        let list = await fetchSessions();
        if (list.length === 0) {
          const id = await createSession();
          list = [
            { id, title: "New chat", updated_at: new Date().toISOString(), message_count: 0 },
          ];
        }
        const details = await Promise.all(
          list.map(async (meta) => ({ meta, detail: await fetchSession(meta.id) })),
        );
        if (cancelled) return;
        serverRef.current = info;
        setServer(info);
        setState((prev) => {
          const sessions = details.map(({ detail }) =>
            mapDetail(detail, prev.userName),
          );
          return {
            ...prev,
            sessions,
            activeId: sessions[0]?.id ?? prev.activeId,
            model: info.model,
            mode: "chat",
          };
        });
        setMetrics((prev) => ({
          ...prev,
          contextMax: info.context_max,
          temperature: info.temperature,
          provider: "llama.cpp",
        }));
      } catch {
        // The API probe succeeded but the store is unreachable; stay local.
        serverRef.current = null;
        setServer(null);
      }
    })().finally(() => {
      // Hold the first paint until we know whether this page is served by the
      // Gemma server, so demo chats never flash before the shared ones.
      if (!cancelled) setBooting(false);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (serverRef.current) return;
    // Debounced so streaming does not rewrite localStorage on every token.
    const timer = window.setTimeout(() => {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
      } catch {
        // Storage may be full or blocked; the UI still works in memory.
      }
    }, 300);
    return () => window.clearTimeout(timer);
  }, [state]);

  const activeSession = useMemo(
    () => state.sessions.find((session) => session.id === state.activeId) ?? state.sessions[0],
    [state.sessions, state.activeId],
  );

  const patchSession = useCallback((sessionId: string, patch: (session: Session) => Session) => {
    setState((prev) => ({
      ...prev,
      sessions: prev.sessions.map((session) =>
        session.id === sessionId ? patch(session) : session,
      ),
    }));
  }, []);

  const patchMessage = useCallback(
    (sessionId: string, messageId: string, patch: (message: Message) => Message) => {
      patchSession(sessionId, (session) => ({
        ...session,
        updatedAt: new Date().toISOString(),
        messages: session.messages.map((message) =>
          message.id === messageId ? patch(message) : message,
        ),
      }));
    },
    [patchSession],
  );

  const addNote = useCallback(
    (text: string) => {
      if (!activeSession) return;
      const message: Message = {
        id: makeId("m"),
        role: "assistant",
        author: "Gemma",
        content: text,
        createdAt: new Date().toISOString(),
        note: true,
      };
      patchSession(activeSession.id, (session) => ({
        ...session,
        updatedAt: new Date().toISOString(),
        messages: [...session.messages, message],
      }));
    },
    [activeSession, patchSession],
  );

  const newChat = useCallback(async () => {
    const now = new Date().toISOString();
    if (serverRef.current) {
      try {
        const id = await createSession();
        const session: Session = {
          id,
          title: "New chat",
          createdAt: now,
          updatedAt: now,
          messages: [],
        };
        setState((prev) => ({
          ...prev,
          sessions: [session, ...prev.sessions],
          activeId: id,
        }));
        setMetrics(emptyMetrics());
        setFocusSignal((n) => n + 1);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        addNote(`**Could not create a chat:** ${message}`);
      }
      return;
    }

    const session: Session = {
      id: makeId(),
      title: "New chat",
      createdAt: now,
      updatedAt: now,
      messages: [],
    };
    setState((prev) => ({
      ...prev,
      sessions: [session, ...prev.sessions],
      activeId: session.id,
    }));
    setMetrics(emptyMetrics());
    setFocusSignal((n) => n + 1);
  }, [addNote]);

  const selectChat = useCallback((id: string) => {
    setState((prev) => ({ ...prev, activeId: id }));
    setFocusSignal((n) => n + 1);
  }, []);

  const deleteChat = useCallback(
    (id: string) => {
      if (id === state.activeId) {
        abortRef.current?.abort();
        abortRef.current = null;
        streamingRef.current = false;
        setStreaming(false);
        setStreamingId(null);
      }

      const remaining = state.sessions.filter((session) => session.id !== id);
      if (remaining.length === 0) {
        void newChat();
      } else {
        setState((prev) => ({
          ...prev,
          sessions: prev.sessions.filter((session) => session.id !== id),
          activeId: prev.activeId === id ? remaining[0].id : prev.activeId,
        }));
      }

      if (serverRef.current) {
        void deleteSession(id).catch(() => {
          // The local view already dropped it; a stale server row is harmless.
        });
      }
    },
    [newChat, state.activeId, state.sessions],
  );

  const clearChat = useCallback(() => {
    if (!activeSession) return;
    const id = activeSession.id;
    patchSession(id, (session) => ({
      ...session,
      title: "New chat",
      updatedAt: new Date().toISOString(),
      messages: [],
    }));
    setMetrics(emptyMetrics());
    if (serverRef.current) {
      void clearSession(id).catch(() => {
        // Keep the local clear even if the server call fails.
      });
    }
  }, [activeSession, patchSession]);

  const renameChat = useCallback(
    (title: string) => {
      if (!activeSession) return;
      const clean = title.trim() || activeSession.title;
      patchSession(activeSession.id, (session) => ({
        ...session,
        title: clean,
        updatedAt: new Date().toISOString(),
      }));
      if (serverRef.current) {
        void renameSession(activeSession.id, clean).catch(() => {
          // The optimistic title stays; the next refresh will reconcile it.
        });
      }
    },
    [activeSession, patchSession],
  );

  const setModel = useCallback((model: string) => {
    setState((prev) => ({ ...prev, model }));
  }, []);

  const setMode = useCallback((mode: Mode) => {
    setState((prev) => ({ ...prev, mode }));
  }, []);

  const setAccent = useCallback((accentId: string) => {
    setState((prev) => ({ ...prev, accentId }));
  }, []);

  const cycleAccent = useCallback(() => {
    setState((prev) => {
      const index = ACCENTS.findIndex((accent) => accent.id === prev.accentId);
      const next = ACCENTS[(index + 1) % ACCENTS.length];
      return { ...prev, accentId: next.id };
    });
  }, []);

  const refreshTitles = useCallback(() => {
    if (!serverRef.current) return;
    void fetchSessions()
      .then((list) => {
        setState((prev) => ({
          ...prev,
          sessions: prev.sessions.map((session) => {
            const meta = list.find((item) => item.id === session.id);
            return meta
              ? { ...session, title: meta.title, updatedAt: meta.updated_at }
              : session;
          }),
        }));
      })
      .catch(() => {
        // Titles are cosmetic; keep the optimistic ones.
      });
  }, []);

  const runCommand = useCallback(
    (input: string): boolean => {
      if (!input.startsWith("/")) return false;
      const [rawName, ...rest] = input.slice(1).split(/\s+/);
      const name = rawName.toLowerCase();
      const arg = rest.join(" ").trim();

      switch (name) {
        case "new":
          void newChat();
          return true;
        case "clear":
          clearChat();
          return true;
        case "model":
          if (serverRef.current) {
            addNote(
              `**Model (server):** \`${serverRef.current.model}\`\n\n` +
                "The web UI uses the model loaded by llama.cpp. " +
                "Change it in the TUI with `/model`.",
            );
          } else if (arg) {
            setModel(arg);
            addNote(`Model set to \`${arg}\`.`);
          } else {
            addNote(
              `**Active model:** \`${state.model}\`\n\n` +
                `Available models:\n${MODELS.map((model) => `- \`${model}\``).join("\n")}\n\n` +
                "Switch with `/model <name>`.",
            );
          }
          return true;
        case "mode": {
          if (serverRef.current) {
            addNote(
              "The web UI is chat-only. Switch to Agent mode in the Gemma TUI " +
                "to run tools in the workspace.",
            );
            return true;
          }
          const next: Mode = state.mode === "agent" ? "chat" : "agent";
          setMode(next);
          addNote(`Mode switched to **${next === "agent" ? "Agent" : "Chat"}**.`);
          return true;
        }
        case "theme": {
          if (arg) {
            const accent = ACCENTS.find((item) => item.id === arg.toLowerCase());
            if (accent) {
              setAccent(accent.id);
              addNote(`Accent set to **${accent.name}**.`);
            } else {
              addNote(`Unknown theme \`${arg}\`. Try ${ACCENTS.map((a) => `\`${a.id}\``).join(", ")}.`);
            }
          } else {
            cycleAccent();
            const index = ACCENTS.findIndex((accent) => accent.id === state.accentId);
            const next = ACCENTS[(index + 1) % ACCENTS.length];
            addNote(`Accent set to **${next.name}**.`);
          }
          return true;
        }
        case "rename":
          if (arg) {
            renameChat(arg);
          } else {
            addNote("Usage: `/rename <title>`");
          }
          return true;
        case "delete":
          if (activeSession) deleteChat(activeSession.id);
          return true;
        case "help":
          addNote(HELP_TEXT);
          return true;
        default:
          addNote(`Unknown command \`/${name}\`. Type \`/help\` for the list.`);
          return true;
      }
    },
    [
      activeSession,
      addNote,
      clearChat,
      cycleAccent,
      deleteChat,
      newChat,
      renameChat,
      setAccent,
      setMode,
      setModel,
      state.accentId,
      state.mode,
      state.model,
    ],
  );

  const send = useCallback(
    async (raw: string) => {
      const text = raw.trim();
      if (!text || streamingRef.current || !activeSession) return;
      if (runCommand(text)) return;

      const useServer = serverRef.current !== null;
      const sessionId = activeSession.id;
      const isFirstMessage = activeSession.messages.length === 0;
      const now = new Date().toISOString();
      const userMessage: Message = {
        id: makeId("m"),
        role: "user",
        author: state.userName,
        content: text,
        createdAt: now,
      };
      const assistantId = makeId("m");
      const assistantMessage: Message = {
        id: assistantId,
        role: "assistant",
        author: "Gemma",
        content: "",
        createdAt: now,
      };

      patchSession(sessionId, (session) => ({
        ...session,
        title: isFirstMessage ? titleFromPrompt(text) : session.title,
        updatedAt: now,
        messages: [...session.messages, userMessage, assistantMessage],
      }));

      const history = [...activeSession.messages, userMessage];
      const baseContext = history.reduce(
        (sum, message) => sum + estimateTokens(message.content),
        0,
      );
      setMetrics((prev) => ({
        ...prev,
        tokens: 0,
        elapsed: 0,
        tokPerSec: 0,
        contextUsed: baseContext,
      }));

      const controller = new AbortController();
      abortRef.current = controller;
      streamingRef.current = true;
      setStreaming(true);
      setStreamingId(assistantId);

      const started = performance.now();
      let firstAt: number | null = null;
      let tokens = 0;
      let content = "";
      let tools: ToolStep[] | undefined;
      let finalElapsed: number | null = null;
      let finalTtft: number | null = null;
      let finalTokens: number | null = null;

      const onToken = (token: string) => {
        if (firstAt === null) firstAt = performance.now();
        content += token;
        tokens += 1;
        patchMessage(sessionId, assistantId, (message) => ({ ...message, content }));
        const elapsed = (performance.now() - started) / 1000;
        const ttft = firstAt ? (firstAt - started) / 1000 : 0;
        const active = Math.max(elapsed - ttft, 0.001);
        setMetrics((prev) => ({
          ...prev,
          tokens,
          elapsed,
          tokPerSec: ttft > 0 ? tokens / active : 0,
          contextUsed: baseContext + estimateTokens(content),
        }));
      };

      try {
        if (useServer) {
          await streamServerChat(sessionId, text, {
            signal: controller.signal,
            onToken,
            onDone: (done) => {
              finalElapsed = done.elapsed;
              finalTtft = done.ttft;
              finalTokens = done.tokens;
            },
          });
        } else {
          await streamReply(history, {
            model: state.model,
            signal: controller.signal,
            onToken,
            onTools: (nextTools) => {
              tools = nextTools;
              patchMessage(sessionId, assistantId, (message) => ({
                ...message,
                tools: nextTools,
              }));
            },
          });
        }

        const elapsed = finalElapsed ?? (performance.now() - started) / 1000;
        const ttft = finalTtft ?? (firstAt ? (firstAt - started) / 1000 : elapsed);
        const finalCount = finalTokens ?? tokens;
        patchMessage(sessionId, assistantId, (message) => ({
          ...message,
          content,
          elapsed,
          ttft,
          tools,
        }));
        setMetrics((prev) => ({
          ...prev,
          elapsed,
          tokens: finalCount,
          tokPerSec: finalCount / Math.max(elapsed - ttft, 0.001),
        }));
        if (useServer) refreshTitles();
      } catch (error) {
        const aborted = error instanceof DOMException && error.name === "AbortError";
        if (aborted) {
          patchMessage(sessionId, assistantId, (message) => ({
            ...message,
            content: content || "_Generation stopped._",
            stopped: true,
          }));
        } else {
          const message = error instanceof Error ? error.message : String(error);
          patchMessage(sessionId, assistantId, (msg) => ({
            ...msg,
            content: content + `\n\n**Error:** ${message}`,
          }));
        }
      } finally {
        abortRef.current = null;
        streamingRef.current = false;
        setStreaming(false);
        setStreamingId(null);
        setFocusSignal((n) => n + 1);
      }
    },
    [
      activeSession,
      patchMessage,
      patchSession,
      refreshTitles,
      runCommand,
      state.model,
      state.userName,
    ],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const accent: AccentPreset =
    ACCENTS.find((item) => item.id === state.accentId) ?? ACCENTS[0];

  // Derive live context usage (and the last reply's timing) from the active
  // session, so switching chats never shows a stale "0 tokens / 0.0s".
  const sessionMetrics = useMemo<Metrics>(() => {
    const provider = server ? "llama.cpp" : hasLiveBackend ? "llama.cpp" : "mock";
    const contextUsed = activeSession.messages.reduce(
      (sum, message) => sum + estimateTokens(message.content),
      0,
    );
    if (streaming) return { ...metrics, contextUsed, provider };

    const lastAssistant = [...activeSession.messages]
      .reverse()
      .find((message) => message.role === "assistant" && typeof message.elapsed === "number");
    if (!lastAssistant) {
      return { ...metrics, contextUsed, provider, elapsed: 0, tokPerSec: 0 };
    }

    const elapsed = lastAssistant.elapsed ?? 0;
    const ttft = lastAssistant.ttft ?? 0;
    const tokens = estimateTokens(lastAssistant.content);
    return {
      ...metrics,
      contextUsed,
      provider,
      elapsed,
      tokens,
      tokPerSec: elapsed > ttft ? tokens / (elapsed - ttft) : metrics.tokPerSec,
    };
  }, [activeSession.messages, metrics, server, streaming]);

  return {
    sessions: state.sessions,
    activeSession,
    activeId: state.activeId,
    model: state.model,
    mode: state.mode,
    accent,
    userName: state.userName,
    metrics: sessionMetrics,
    streaming,
    streamingId,
    focusSignal,
    booting,
    serverMode: server !== null,
    serverInfo: server,
    agentAvailable: server === null,
    send,
    stop,
    newChat,
    selectChat,
    deleteChat,
    clearChat,
    renameChat,
    setModel,
    setMode,
    setAccent,
  };
}

export type ChatController = ReturnType<typeof useChat>;
