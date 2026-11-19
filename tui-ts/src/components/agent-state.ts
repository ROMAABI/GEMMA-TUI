import { createMemo, type Accessor } from "solid-js"
import type { Message, Part, SearchState } from "../chat"
import type { AgentPhase } from "./pacman"

export interface AgentExecutionState {
  phase: AgentPhase
  stepLog: string
  elapsed: number
  tokensPerSecond: number
  activeToolName?: string
}

/**
 * Derive dynamic live agent execution state:
 * - phase: 'thinking' | 'searching' | 'reading' | 'writing' | 'bash'
 * - stepLog: active operation summary with context
 * - elapsed: live turn execution time
 * - tokensPerSecond: live generation speed
 */
export function deriveAgentState(
  streaming: boolean,
  activeMessage: Message | undefined,
  liveMetrics: { tokens: number; elapsed: number; ttft: number; genMs?: number },
  tokPerSec: number,
): AgentExecutionState {
  if (!streaming || !activeMessage) {
    return {
      phase: "thinking",
      stepLog: "ready",
      elapsed: 0,
      tokensPerSecond: 0,
    }
  }

  const elapsed = liveMetrics.elapsed > 0 ? liveMetrics.elapsed : (activeMessage.elapsed ?? 0)
  const tokensPerSecond = tokPerSec

  // 1. Check websearch state
  if (activeMessage.search) {
    const s = activeMessage.search
    if (s.status === "searching") {
      const q = s.query ? `query: ${s.query}` : "querying SearXNG"
      return {
        phase: "searching",
        stepLog: q,
        elapsed,
        tokensPerSecond,
      }
    }
    if (s.status === "reading") {
      const count = s.count ?? s.sources?.length ?? 0
      const detail = count > 0 ? `reading ${count} pages` : "reading search results"
      return {
        phase: "reading",
        stepLog: detail,
        elapsed,
        tokensPerSecond,
      }
    }
  }

  // 2. Check running tool
  const runningTool = activeMessage.parts.find(
    (p): p is Extract<Part, { kind: "tool" }> => p.kind === "tool" && p.status === "running",
  )
  if (runningTool) {
    const name = runningTool.name.toLowerCase()
    const rawArgs = runningTool.args ?? {}
    const argSummary = String(
      rawArgs.command ??
      rawArgs.query ??
      rawArgs.path ??
      rawArgs.url ??
      rawArgs.file_path ??
      rawArgs.pattern ??
      ""
    ).trim()
    const cleanArg = argSummary.replace(/\s+/g, " ")
    const shortArg = cleanArg.length > 36 ? `${cleanArg.slice(0, 35)}…` : cleanArg

    if (name.includes("bash") || name.includes("command") || name.includes("exec")) {
      return {
        phase: "bash",
        stepLog: shortArg ? `exec: ${shortArg}` : `exec ${runningTool.name}`,
        elapsed,
        tokensPerSecond,
        activeToolName: runningTool.name,
      }
    }
    if (name.includes("search") || name.includes("find") || name.includes("grep")) {
      return {
        phase: "searching",
        stepLog: shortArg ? `search: ${shortArg}` : `search ${runningTool.name}`,
        elapsed,
        tokensPerSecond,
        activeToolName: runningTool.name,
      }
    }
    if (name.includes("read") || name.includes("view") || name.includes("fetch")) {
      return {
        phase: "reading",
        stepLog: shortArg ? `read: ${shortArg}` : `read ${runningTool.name}`,
        elapsed,
        tokensPerSecond,
        activeToolName: runningTool.name,
      }
    }
    // Default tool phase
    return {
      phase: "bash",
      stepLog: shortArg ? `${runningTool.name}: ${shortArg}` : `tool: ${runningTool.name}`,
      elapsed,
      tokensPerSecond,
      activeToolName: runningTool.name,
    }
  }

  // 3. Inspect message parts: reasoning vs text streaming
  const lastPart = activeMessage.parts[activeMessage.parts.length - 1]
  if (lastPart?.kind === "reasoning") {
    const text = lastPart.text.trim()
    const lastLine = text ? (text.split("\n").filter(Boolean).pop() ?? "reasoning") : "understanding intent & architecture context"
    const clean = lastLine.replace(/\s+/g, " ")
    const short = clean.length > 42 ? `${clean.slice(0, 41)}…` : clean
    return {
      phase: "thinking",
      stepLog: short,
      elapsed,
      tokensPerSecond,
    }
  }

  if (lastPart?.kind === "text") {
    const tokStr = tokensPerSecond > 0 ? ` · ${Math.round(tokensPerSecond)} tok/s` : ""
    return {
      phase: "writing",
      stepLog: `streaming response${tokStr}`,
      elapsed,
      tokensPerSecond,
    }
  }

  return {
    phase: "thinking",
    stepLog: "understanding intent & architecture context",
    elapsed,
    tokensPerSecond,
  }
}
