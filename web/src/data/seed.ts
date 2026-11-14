import type { Session } from "../types";

const minutesAgo = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

export function seedSessions(): Session[] {
  const parser: Session = {
    id: "a1f3c9d2e7b4",
    title: "Refactor the parser and make sure the tests pass",
    createdAt: minutesAgo(74),
    updatedAt: minutesAgo(62),
    messages: [
      {
        id: "m-parser-1",
        role: "user",
        author: "Spix",
        content: "Refactor the parser and make sure the tests pass.",
        createdAt: minutesAgo(64),
      },
      {
        id: "m-parser-2",
        role: "assistant",
        author: "Gemma",
        content:
          "I'll start by reading the parser module.\n\n```python\ndef parse(source: str) -> Node:\n    tokens = tokenize(source)\n    return Parser(tokens).document()\n```\n\nThen I'll run the test suite.",
        createdAt: minutesAgo(63),
        elapsed: 12.4,
        ttft: 1.2,
        tools: [
          {
            id: "t-parser-1",
            step: 2,
            tool: "bash",
            target: "pytest -q",
            status: "done",
            output: "12 passed in 0.41s",
          },
        ],
      },
    ],
  };

  const hello: Session = {
    id: "b7e21a4c9f08",
    title: "Hi there! How can I help you today?",
    createdAt: minutesAgo(38),
    updatedAt: minutesAgo(37),
    messages: [
      {
        id: "m-hello-1",
        role: "user",
        author: "Spix",
        content: "hey gemma",
        createdAt: minutesAgo(38),
      },
      {
        id: "m-hello-2",
        role: "assistant",
        author: "Gemma",
        content: "Hi! How can I help you today? 😊",
        createdAt: minutesAgo(37),
        elapsed: 6.4,
        ttft: 1.4,
      },
    ],
  };

  return [parser, hello];
}

export const WELCOME_SUGGESTIONS = [
  {
    title: "Explain a concept",
    prompt: "Explain how a transformer attention block works, in simple terms.",
  },
  {
    title: "Write a function",
    prompt: "Write a Python function that batches an iterable into chunks of n.",
  },
  {
    title: "Audit this repo",
    prompt: "Audit the current workspace for TODOs and summarize what needs attention.",
  },
];
