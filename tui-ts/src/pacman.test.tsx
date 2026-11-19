import { describe, it, expect } from "bun:test";
import { PACMAN_PALETTE, PHASE_CONFIG, getPacManChar, getGhostChar, getGhostColor } from "./components/pacman";
import { deriveAgentState } from "./components/agent-state";
import type { Message } from "./chat";

describe("Pac-Man Arcade Suite", () => {
  it("has the required character & phase color palette", () => {
    expect(PACMAN_PALETTE.pacman).toBe("#FFD700");
    expect(PACMAN_PALETTE.inky).toBe("#00E5FF");
    expect(PACMAN_PALETTE.clyde).toBe("#FFB852");
    expect(PACMAN_PALETTE.pinky).toBe("#FFB8FF");
    expect(PACMAN_PALETTE.blinky).toBe("#FF3B30");
    expect(PACMAN_PALETTE.frightened).toBe("#2121FF");
    expect(PACMAN_PALETTE.frightenedWhite).toBe("#FFFFFF");
    expect(PACMAN_PALETTE.mazeBlue).toBe("#1919A6");
    expect(PACMAN_PALETTE.successGreen).toBe("#32D74B");
  });

  it("maps agent phases to the correct ghosts and colors", () => {
    expect(PHASE_CONFIG.thinking.ghostName).toBe("Inky");
    expect(PHASE_CONFIG.thinking.color).toBe("#00E5FF");

    expect(PHASE_CONFIG.searching.ghostName).toBe("Clyde");
    expect(PHASE_CONFIG.searching.color).toBe("#FFB852");

    expect(PHASE_CONFIG.reading.ghostName).toBe("Pinky");
    expect(PHASE_CONFIG.reading.color).toBe("#FFB8FF");

    expect(PHASE_CONFIG.bash.ghostName).toBe("Blinky");
    expect(PHASE_CONFIG.bash.color).toBe("#FF3B30");

    expect(PHASE_CONFIG.writing.ghostName).toBe("Frightened Ghost");
    // writing alternates between frightened blue and white (cycle length 6, <3 blue, >=3 white)
    expect(getGhostColor("writing", 0)).toBe("#2121FF");
    expect(getGhostColor("writing", 4)).toBe("#FFFFFF");
  });

  it("animates Pac-Man mouth positions and directions", () => {
    // direction +1 (right)
    const ch0 = getPacManChar(1, 0);
    const ch1 = getPacManChar(1, 1);
    expect(["ᗧ", "C", "O"]).toContain(ch0);
    expect(["ᗧ", "C", "O"]).toContain(ch1);

    // direction -1 (left)
    const chLeft0 = getPacManChar(-1, 0);
    const chLeft1 = getPacManChar(-1, 1);
    expect(["ᗤ", "◖", "O"]).toContain(chLeft0);
    expect(["ᗤ", "◖", "O"]).toContain(chLeft1);
  });

  it("derives dynamic agent states from live execution", () => {
    const baseMetrics = { tokens: 0, elapsed: 1.5, ttft: 0.2 };

    // 1. Thinking state
    const msgThinking: Message = {
      id: "1",
      role: "assistant",
      content: "",
      createdAt: new Date().toISOString(),
      parts: [{ kind: "reasoning", text: "Analyzing codebase architecture..." }],
    };
    const s1 = deriveAgentState(true, msgThinking, { ...baseMetrics, tokens: 10 }, 15);
    expect(s1.phase).toBe("thinking");
    expect(s1.stepLog).toContain("Analyzing codebase architecture");
    expect(s1.tokensPerSecond).toBe(15);

    // 2. Searching state
    const msgSearch: Message = {
      id: "2",
      role: "assistant",
      content: "",
      createdAt: new Date().toISOString(),
      parts: [],
      search: { query: "pacman tui specs", status: "searching" },
    };
    const s2 = deriveAgentState(true, msgSearch, baseMetrics, 0);
    expect(s2.phase).toBe("searching");
    expect(s2.stepLog).toContain("pacman tui specs");

    // 3. Bash / tool execution state
    const msgBash: Message = {
      id: "3",
      role: "assistant",
      content: "",
      createdAt: new Date().toISOString(),
      parts: [{
        kind: "tool",
        id: "call_1",
        name: "bash",
        args: { command: "bun test" },
        status: "running",
        startedAt: Date.now(),
      }],
    };
    const s3 = deriveAgentState(true, msgBash, baseMetrics, 0);
    expect(s3.phase).toBe("bash");
    expect(s3.stepLog).toContain("bun test");

    // 4. Reading state
    const msgReading: Message = {
      id: "4",
      role: "assistant",
      content: "",
      createdAt: new Date().toISOString(),
      parts: [{
        kind: "tool",
        id: "call_2",
        name: "read_file",
        args: { path: "/etc/hosts" },
        status: "running",
        startedAt: Date.now(),
      }],
    };
    const s4 = deriveAgentState(true, msgReading, baseMetrics, 0);
    expect(s4.phase).toBe("reading");
    expect(s4.stepLog).toContain("/etc/hosts");

    // 5. Writing / streaming state
    const msgWriting: Message = {
      id: "5",
      role: "assistant",
      content: "Generating the solution code...",
      createdAt: new Date().toISOString(),
      parts: [{ kind: "text", text: "Generating the solution code..." }],
    };
    const s5 = deriveAgentState(true, msgWriting, { ...baseMetrics, tokens: 120, elapsed: 4.5 }, 32.5);
    expect(s5.phase).toBe("writing");
    expect(s5.stepLog).toContain("streaming response");
    expect(s5.stepLog).toContain("tok/s");
    expect(s5.tokensPerSecond).toBe(32.5);
  });
});
