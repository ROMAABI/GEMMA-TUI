import type { CommandDef } from "./api"

/** Commands this terminal client implements. */
export const TUI_COMMANDS: CommandDef[] = [
  { name: "/new", description: "Start a new session" },
  { name: "/clear", description: "Clear this session" },
  { name: "/model", description: "Pick a provider model" },
  { name: "/mode", description: "Switch chat / agent mode" },
  { name: "/agent", description: "Switch to agent mode" },
  { name: "/chat", description: "Switch to chat mode" },
  { name: "/providers", description: "List configured providers" },
  { name: "/theme", description: "Pick a theme" },
  { name: "/rename", description: "Rename this session" },
  { name: "/delete", description: "Delete this session" },
  { name: "/copy", description: "Copy the last reply" },
  { name: "/stats", description: "Show session stats" },
  { name: "/web", description: "Pair a browser (prints a link)" },
  { name: "/classic", description: "Open the Textual TUI instead" },
  { name: "/help", description: "Show commands and keys" },
  { name: "/exit", description: "Quit" },
]

/** Keep the client's wording, but take fresher descriptions from the server. */
export function mergeCommands(server: CommandDef[]): CommandDef[] {
  const byName = new Map<string, string>(
    server.map((command) => [command.name, command.description] as [string, string]),
  )
  return TUI_COMMANDS.map((command) => ({
    ...command,
    description: byName.get(command.name) ?? command.description,
  }))
}
