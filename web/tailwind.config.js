/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#121212",
        panel: "#181818",
        card: "#1a1a1a",
        line: "#2a2a2a",
        text: "#e5e7eb",
        dim: "#9ca3af",
        muted: "#6b7280",
        accent: "rgb(var(--accent) / <alpha-value>)",
        success: "#10b981",
        danger: "#ef4444",
        warning: "#f59e0b",
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
        mono: [
          "JetBrains Mono",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
      boxShadow: {
        card: "0 1px 0 0 rgba(255,255,255,0.02) inset, 0 8px 24px -18px rgba(0,0,0,0.9)",
        glow: "0 0 0 1px rgb(var(--accent) / 0.35), 0 0 24px -8px rgb(var(--accent) / 0.35)",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(6px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        blink: {
          "0%, 45%": { opacity: "1" },
          "50%, 100%": { opacity: "0" },
        },
      },
      animation: {
        "fade-up": "fade-up 220ms ease-out both",
        blink: "blink 1s step-end infinite",
      },
    },
  },
  plugins: [],
};
