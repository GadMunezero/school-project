import type { Config } from "tailwindcss";

/**
 * Design tokens.
 *
 * Colours are declared as CSS variables in `globals.css` and referenced here, so a single
 * `.dark` class swap re-themes the whole product without duplicating the palette.
 *
 * Profit/loss use a teal-leaning green and a rose-leaning red rather than pure hues: both hold
 * AA contrast on either background, and the shift away from pure green/red keeps them
 * distinguishable for the most common forms of colour blindness while still reading as
 * "up/down" to a trader.
 */
const config: Config = {
  darkMode: "class",
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "rgb(var(--canvas) / <alpha-value>)",
        surface: "rgb(var(--surface) / <alpha-value>)",
        raised: "rgb(var(--raised) / <alpha-value>)",
        line: "rgb(var(--line) / <alpha-value>)",
        ink: "rgb(var(--ink) / <alpha-value>)",
        muted: "rgb(var(--muted) / <alpha-value>)",
        faint: "rgb(var(--faint) / <alpha-value>)",
        accent: "rgb(var(--accent) / <alpha-value>)",
        "accent-ink": "rgb(var(--accent-ink) / <alpha-value>)",
        profit: "rgb(var(--profit) / <alpha-value>)",
        loss: "rgb(var(--loss) / <alpha-value>)",
        warn: "rgb(var(--warn) / <alpha-value>)",
        info: "rgb(var(--info) / <alpha-value>)",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        // Tabular figures matter: a column of P&L must align on the decimal point.
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
      borderRadius: {
        DEFAULT: "0.5rem",
      },
      boxShadow: {
        card: "0 1px 2px 0 rgb(0 0 0 / 0.04), 0 1px 3px 0 rgb(0 0 0 / 0.06)",
        pop: "0 4px 16px -2px rgb(0 0 0 / 0.12), 0 2px 6px -2px rgb(0 0 0 / 0.08)",
      },
      keyframes: {
        shimmer: { "100%": { transform: "translateX(100%)" } },
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
      },
      animation: {
        shimmer: "shimmer 1.6s infinite",
        "fade-in": "fade-in 150ms ease-out",
      },
    },
  },
  plugins: [],
};

export default config;
