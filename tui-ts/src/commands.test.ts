import { describe, expect, test } from "bun:test"
import { TUI_COMMANDS, mergeCommands } from "./commands"
import type { CommandDef } from "./api"

describe("mergeCommands", () => {
  test("keeps the client's wording when the server does not know a command", () => {
    const merged = mergeCommands([{ name: "/new", description: "Server wording" }])
    const fresh = merged.find((command) => command.name === "/new")
    expect(fresh?.description).toBe("Server wording")
    const local = merged.find((command) => command.name === "/rename")
    expect(local?.description).toBe(TUI_COMMANDS.find((c) => c.name === "/rename")?.description)
  })

  test("appends server commands the client does not implement", () => {
    const server: CommandDef[] = [
      { name: "/new", description: "Start a new chat" },
      { name: "/permissions", description: "Inspect or change permission rules" },
      { name: "/mcp", description: "Manage MCP servers and tools" },
    ]
    const merged = mergeCommands(server)
    expect(merged.some((command) => command.name === "/permissions")).toBe(true)
    expect(merged.some((command) => command.name === "/mcp")).toBe(true)
    // No duplicates for commands both sides know.
    expect(merged.filter((command) => command.name === "/new")).toHaveLength(1)
  })

  test("matches server names that carry argument hints", () => {
    const server: CommandDef[] = [
      { name: "/rename <name>", description: "Rename current session" },
      { name: "/sidebar [show|hide|toggle]", description: "Toggle sidebar (Ctrl+B)" },
      { name: "/mcp [status|start|reload|stop]", description: "Manage MCP servers and tools" },
    ]
    const merged = mergeCommands(server)
    expect(merged.filter((command) => command.name.startsWith("/rename"))).toHaveLength(1)
    expect(merged.find((command) => command.name === "/rename")?.description).toBe(
      "Rename current session",
    )
    expect(merged.some((command) => command.name === "/sidebar [show|hide|toggle]")).toBe(false)
    // Server-only commands keep their base name; the hint moves to the description.
    const mcp = merged.find((command) => command.name === "/mcp")
    expect(mcp?.description).toBe("Manage MCP servers and tools ([status|start|reload|stop])")
  })
})
