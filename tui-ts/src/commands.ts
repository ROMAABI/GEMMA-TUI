import type { CommandDef } from "./api"

/** Commands this terminal client implements. */
export const TUI_COMMANDS: CommandDef[] = [
  { name: "/new", description: "Start a new session" },
  { name: "/stop", description: "Cancel the running agent turn" },
  { name: "/attach", description: "Attach an image, video, PDF or text file" },
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
  { name: "/logs", description: "View the server log tail (ctrl+x l)" },
  { name: "/stats", description: "Show session stats" },
  { name: "/sidebar", description: "Toggle or show/hide the sidebar (ctrl+b)" },
  { name: "/history", description: "List saved sessions" },
  { name: "/config", description: "Show current config" },
  { name: "/web", description: "Pair a browser (prints a link)" },
  { name: "/classic", description: "Open the Textual TUI instead" },
  { name: "/help", description: "Show commands and keys" },
  { name: "/exit", description: "Quit" },
]

/**
 * Keep the client's wording, but take fresher descriptions from the server and
 * append everything else it advertises (so the palette and /help never hide a
 * command the backend understands). Server names may carry argument hints
 * ("/rename <name>"); merge on the base name and keep the client's clean form.
 */
export function mergeCommands(server: CommandDef[]): CommandDef[] {
  const base = (name: string) => name.split(/\s+/)[0] ?? name
  const clientNames = new Set(TUI_COMMANDS.map((command) => base(command.name)))
  const client = TUI_COMMANDS.map((command) => {
    const fromServer = server.find((candidate) => base(candidate.name) === base(command.name))
    return fromServer ? { ...command, description: fromServer.description } : command
  })
  const extra = server
    .filter((command) => !clientNames.has(base(command.name)))
    .map((command) => {
      // Move argument hints out of the name so palette submit matches exactly.
      const name = base(command.name)
      const hint = command.name.slice(name.length).trim()
      return hint
        ? { name, description: `${command.description} (${hint})` }
        : { name, description: command.description }
    })
  return [...client, ...extra]
}
