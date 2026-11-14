const numberFmt = new Intl.NumberFormat("en-US");

export function formatNumber(value: number): string {
  return numberFmt.format(Math.max(0, Math.round(value)));
}

export function formatClock(date: Date): string {
  return date.toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
  });
}

export function formatMessageTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

export function timeAgo(iso: string): string {
  const seconds = Math.max(1, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function shortSessionId(id: string): string {
  return id.slice(0, 8);
}

export function titleFromPrompt(prompt: string, limit = 42): string {
  const clean = prompt.replace(/\s+/g, " ").trim();
  if (!clean) return "New chat";
  return clean.length > limit ? `${clean.slice(0, limit - 1)}…` : clean;
}

export function estimateTokens(text: string): number {
  return Math.ceil(text.length / 4);
}
