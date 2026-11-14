import { useCallback, useEffect, useState } from "react";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { ChatFeed } from "./components/ChatFeed";
import { Composer } from "./components/Composer";
import { useChat } from "./hooks/useChat";
import { useClock } from "./hooks/useClock";
import { useConnection } from "./hooks/useConnection";

export default function App() {
  const chat = useChat();
  const now = useClock();
  const connected = useConnection();
  const [drawerOpen, setDrawerOpen] = useState(false);

  const { newChat, accent, activeSession } = chat;

  useEffect(() => {
    document.documentElement.style.setProperty("--accent", accent.rgb);
  }, [accent.rgb]);

  useEffect(() => {
    document.title = `${activeSession.title} · Gemma`;
  }, [activeSession.title]);

  const closeDrawer = useCallback(() => setDrawerOpen(false), []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "n") {
        event.preventDefault();
        newChat();
        setDrawerOpen(false);
      }
      if (event.key === "Escape") setDrawerOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [newChat]);

  const sidebar = (onClose?: () => void) => (
    <Sidebar
      sessions={chat.sessions}
      activeId={chat.activeId}
      accent={chat.accent}
      streaming={chat.streaming}
      onNew={() => {
        chat.newChat();
        onClose?.();
      }}
      onSelect={(id) => {
        chat.selectChat(id);
        onClose?.();
      }}
      onDelete={chat.deleteChat}
      onAccent={chat.setAccent}
      onClose={onClose}
    />
  );

  if (chat.booting) {
    return (
      <div className="grid h-full w-full place-items-center">
        <div className="flex items-center gap-2.5 text-[12px] text-muted">
          <span className="h-2 w-2 animate-pulse rounded-full bg-accent" />
          Connecting to Gemma…
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full w-full gap-3 p-3">
      <aside className="hidden w-[272px] shrink-0 lg:block">{sidebar()}</aside>

      <main className="flex min-w-0 flex-1 flex-col gap-3">
        <TopBar
          model={chat.model}
          mode={chat.mode}
          connected={connected}
          now={now}
          onModel={chat.setModel}
          onMode={chat.setMode}
          onMenu={() => setDrawerOpen(true)}
          serverMode={chat.serverMode}
          modelLocked={chat.serverMode}
          agentDisabled={!chat.agentAvailable}
        />
        <ChatFeed
          session={chat.activeSession}
          streaming={chat.streaming}
          streamingId={chat.streamingId}
          onSend={(text) => void chat.send(text)}
        />
        <Composer
          onSend={(text) => void chat.send(text)}
          onStop={chat.stop}
          streaming={chat.streaming}
          metrics={chat.metrics}
          focusSignal={chat.focusSignal}
        />
      </main>

      {drawerOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            type="button"
            aria-label="Close menu"
            onClick={closeDrawer}
            className="absolute inset-0 h-full w-full bg-black/65 backdrop-blur-sm"
          />
          <div className="absolute inset-y-0 left-0 w-[290px] max-w-[85vw] p-3">{sidebar(closeDrawer)}</div>
        </div>
      )}
    </div>
  );
}
