import { describe, expect, test } from "bun:test"
import { inline, parseMarkdown } from "./markdown"

describe("parseMarkdown", () => {
  test("reads headings, paragraphs and blank-line separation", () => {
    const blocks = parseMarkdown("# Title\n\nHello there.\nSecond line.")
    expect(blocks[0]).toEqual({ kind: "heading", level: 1, text: "Title" })
    expect(blocks[1]).toEqual({ kind: "paragraph", text: "Hello there.\nSecond line." })
  })

  test("reads fenced code and keeps the language", () => {
    const blocks = parseMarkdown("Before\n\n```python\nprint('hi')\n```\n\nAfter")
    expect(blocks.map((block) => block.kind)).toEqual(["paragraph", "code", "paragraph"])
    expect(blocks[1]).toEqual({ kind: "code", lang: "python", text: "print('hi')" })
  })

  test("reads ordered and bulleted lists separately", () => {
    const blocks = parseMarkdown("- one\n- two\n\n1. first\n2. second")
    expect(blocks[0]).toEqual({ kind: "list", ordered: false, items: ["one", "two"] })
    expect(blocks[1]).toEqual({ kind: "list", ordered: true, items: ["first", "second"] })
  })

  test("reads quotes, tables and rules", () => {
    const blocks = parseMarkdown("> quoted\n\n| a | b |\n| - | - |\n\n---")
    expect(blocks[0]).toEqual({ kind: "quote", text: "quoted" })
    expect(blocks[1]?.kind).toBe("table")
    expect(blocks[2]).toEqual({ kind: "hr" })
  })

  test("keeps an unterminated fence as code to the end", () => {
    const blocks = parseMarkdown("```js\nlet a = 1")
    expect(blocks).toEqual([{ kind: "code", lang: "js", text: "let a = 1" }])
  })
})

describe("inline", () => {
  test("splits bold, italic, code and links", () => {
    const segments = inline("a **b** _c_ `d` [e](https://x.test) f")
    expect(segments).toEqual([
      { text: "a " },
      { text: "b", bold: true },
      { text: " " },
      { text: "c", italic: true },
      { text: " " },
      { text: "d", code: true },
      { text: " " },
      { text: "e", link: true },
      { text: " f" },
    ])
  })

  test("leaves unmatched markers as text", () => {
    expect(inline("2 * 3 = 6 and a_b")).toEqual([{ text: "2 * 3 = 6 and a_b" }])
  })

  test("does not italicize intraword underscores or asterisks", () => {
    // The classic breakage: "Gemini (language model)" style text plus
    // snake_case and URLs that contain underscores.
    expect(inline("Gemini (language_model) is a model")).toEqual([
      { text: "Gemini (language_model) is a model" },
    ])
    expect(inline("model_name_v3 runs fine")).toEqual([{ text: "model_name_v3 runs fine" }])
    expect(inline("see https://x.test/wiki/Gemini_(language_model) now")).toEqual([
      { text: "see https://x.test/wiki/Gemini_(language_model) now" },
    ])
    // Real emphasis still works mid-sentence and around punctuation.
    expect(inline("this is (very) *fast*")).toEqual([
      { text: "this is (very) " },
      { text: "fast", italic: true },
    ])
  })
})
