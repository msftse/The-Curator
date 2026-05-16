import type { Config } from "tailwindcss";

/**
 * Design tokens for Agentic Skill Hub — "Curated Intelligence" palette.
 *
 * Roles:
 *   Deep background  Obsidian Navy    #0B1020   → ink.DEFAULT / ink.dark
 *   Surface          Slate Ink        #171B2E   → ink.surface / ink.dark-2
 *   Primary          Curator Indigo   #5B5FEF   → ms-blue (kept name for diff-friendliness)
 *   Secondary        Signal Violet    #8B5CF6   → violet (new token; also legacy ms-red slot)
 *   Accent           Artifact Gold    #F5C542   → gold / ms-yellow
 *   Text             Porcelain White  #F8FAFC   → cream.DEFAULT
 *   Muted text       Mist Gray        #94A3B8   → muted.DEFAULT
 *
 * Status colors (semantic — kept punchy and accessible on navy):
 *   approved → emerald  #10B981  (ms-green)
 *   rejected → red      #EF4444  (legacy danger; not in primary palette)
 *   pending  → gold     #F5C542  (ms-yellow)
 *   classified → indigo #5B5FEF  (ms-blue)
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "Segoe UI Variable",
          "Segoe UI",
          "-apple-system",
          "BlinkMacSystemFont",
          "SF Pro Text",
          "system-ui",
          "sans-serif",
        ],
        display: [
          "Segoe UI Variable Display",
          "Segoe UI",
          "-apple-system",
          "BlinkMacSystemFont",
          "system-ui",
          "sans-serif",
        ],
        mono: [
          "SF Mono",
          "Cascadia Mono",
          "Menlo",
          "ui-monospace",
          "monospace",
        ],
      },
      colors: {
        // Brand palette (token names preserved so existing className usages keep working).
        ms: {
          // Primary — Curator Indigo
          blue: "#5B5FEF",
          "blue-dark": "#4548D9",
          "blue-darker": "#3538B5",
          // Status accents — repurposed for the new palette
          red: "#EF4444",     // rejected / danger dot
          green: "#10B981",   // approved
          yellow: "#F5C542",  // pending / Artifact Gold
        },
        // Signal Violet — secondary brand accent (new)
        violet: {
          DEFAULT: "#8B5CF6",
          dark: "#7C3AED",
          dim: "rgba(139,92,246,0.18)",
        },
        // Ink — Obsidian Navy + Slate Ink surfaces
        ink: {
          DEFAULT: "#0B1020",   // Obsidian Navy — deep background
          2: "#1E2340",         // raised text / softer ink
          dark: "#0B1020",
          "dark-2": "#171B2E",  // Slate Ink — surface
          surface: "#171B2E",
        },
        // Cream — Porcelain White text on dark
        cream: {
          DEFAULT: "#F8FAFC",
          dim: "rgba(248,250,252,0.72)",
          border: "rgba(248,250,252,0.32)",
        },
        // Gold — Artifact Gold accent
        gold: {
          DEFAULT: "#F5C542",
          dim: "rgba(245,197,66,0.32)",
        },
        // Light surfaces — cool slate tinted to harmonize with indigo/navy
        bg: {
          DEFAULT: "#F8FAFC",   // Porcelain White as the page bg
          2: "#EEF1F7",         // soft slate-tint card bg
          warm: "#F3F1FB",      // very faint violet wash (replaces the old cream warm)
        },
        line: {
          DEFAULT: "#E2E8F0",   // slate-200
          2: "#CBD5E1",         // slate-300
        },
        muted: {
          DEFAULT: "#94A3B8",   // Mist Gray
          2: "#64748B",         // slate-500 for stronger muted text
        },
        // Tonal status (used by badges/msgbars). Adjusted for the cool palette.
        success: {
          bg: "rgba(16,185,129,0.10)",
          fg: "#047857",
          border: "rgba(16,185,129,0.32)",
        },
        warning: {
          bg: "rgba(245,197,66,0.14)",
          fg: "#8A6500",
          border: "rgba(245,197,66,0.40)",
        },
        danger: {
          bg: "rgba(239,68,68,0.10)",
          fg: "#B91C1C",
          border: "rgba(239,68,68,0.32)",
        },
        info: {
          bg: "rgba(91,95,239,0.10)",
          fg: "#3538B5",
          border: "rgba(91,95,239,0.28)",
        },
      },
      borderRadius: {
        none: "0",
        sm: "4px",
        DEFAULT: "6px",
        md: "8px",
        lg: "14px",
        xl: "16px",
        "2xl": "20px",
      },
      boxShadow: {
        "ms-sm": "0 1px 2px rgba(11,16,32,.06), 0 0 1px rgba(11,16,32,.08)",
        "ms-md": "0 4px 12px rgba(11,16,32,.08), 0 0 1px rgba(11,16,32,.10)",
        "ms-lg":
          "0 18px 50px rgba(91,95,239,.18), 0 4px 14px rgba(11,16,32,.10)",
      },
      letterSpacing: {
        "ms-tight": "-0.02em",
        "ms-display": "-0.01em",
        "ms-track": "0.18em",
        "ms-hero": "0.25em",
      },
      backgroundImage: {
        // Curator gradient: Signal Violet → Curator Indigo → Artifact Gold.
        // Replaces the old 4-color MS stripe; keep the token name so existing
        // utility classes (.ms-gradient-stripe, .ms-accent-text) still work.
        "ms-gradient":
          "linear-gradient(90deg, #8B5CF6 0%, #5B5FEF 55%, #F5C542 100%)",
      },
      keyframes: {
        "ms-shine": {
          "0%": { backgroundPosition: "0% 0%" },
          "100%": { backgroundPosition: "200% 0%" },
        },
        "pulse-dot": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.4" },
        },
      },
      animation: {
        "ms-shine": "ms-shine 8s linear infinite",
        "pulse-dot": "pulse-dot 2s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;
