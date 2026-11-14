export type Role = "user" | "assistant";

export type Mode = "chat" | "agent";

export type ToolStatus = "running" | "done" | "error";

export interface ToolStep {
  id: string;
  step: number;
  tool: string;
  target: string;
  status: ToolStatus;
  output?: string;
}

export interface Message {
  id: string;
  role: Role;
  author: string;
  content: string;
  createdAt: string;
  elapsed?: number;
  ttft?: number;
  tools?: ToolStep[];
  /** A short in-feed system note (command results, errors). */
  note?: boolean;
  stopped?: boolean;
}

export interface Session {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  messages: Message[];
}

export interface Metrics {
  contextUsed: number;
  contextMax: number;
  tokens: number;
  elapsed: number;
  tokPerSec: number;
  provider: string;
  temperature: number;
}

export interface AccentPreset {
  id: string;
  name: string;
  rgb: string;
}

export const ACCENTS: AccentPreset[] = [
  { id: "cyan", name: "Cyan", rgb: "34 211 238" },
  { id: "violet", name: "Violet", rgb: "167 139 250" },
  { id: "emerald", name: "Emerald", rgb: "52 211 153" },
  { id: "amber", name: "Amber", rgb: "251 191 36" },
];

export const MODELS = [
  "gemma-4-E4B-it-ultra-uncensored-heretic-Q4_K_M",
  "gemma-4-E4B-it-mmproj-BF16",
  "gemma-3-12b-it-Q4_K_M",
];

export const CONTEXT_MAX = 32_768;
