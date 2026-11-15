import { describe, expect, test } from "bun:test"
import { displayModelName } from "./format"

describe("displayModelName", () => {
  test("collapses gemma-4 model ids to a short name", () => {
    expect(displayModelName("gemma-4-E4B-it-ultra-uncensored-heretic-Q4_K_M")).toBe("gemma 4")
    expect(displayModelName("gemma-4-E4B-it-ultra-uncensored-heretic-Q4_K_M.gguf")).toBe("gemma 4")
    expect(displayModelName("/models/gemma-4-E4B-it-ultra-uncensored-heretic-Q4_K_M.gguf")).toBe(
      "gemma 4",
    )
    expect(displayModelName("gemma-4")).toBe("gemma 4")
    expect(displayModelName("Gemma-4B")).toBe("gemma 4")
  })

  test("keeps the size for other gemma generations", () => {
    expect(displayModelName("gemma-3-4b-it-Q4_K_M")).toBe("gemma 3 4b")
  })

  test("strips quantisation and fine-tune noise from other models", () => {
    expect(displayModelName("llama-3.1-8b-instruct-Q4_K_M.gguf")).toBe("llama 3.1 8b instruct")
    expect(displayModelName("qwen2.5-coder-7b-uncensored-heretic-IQ4_XS")).toBe(
      "qwen2.5 coder 7b",
    )
    expect(displayModelName("mmproj-F16.gguf")).toBe("mmproj")
  })

  test("returns an empty string for empty input", () => {
    expect(displayModelName("")).toBe("")
    expect(displayModelName("   ")).toBe("")
  })
})
