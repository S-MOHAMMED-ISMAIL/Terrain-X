/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      // TERRAIN-X design system: `slate` stays the neutral/base scale
      // already used throughout the app (unchanged, so no existing
      // `text-slate-*`/`bg-slate-*` class needs touching); `brand` is the
      // one new accent — a technical, geospatial teal — reserved for
      // primary actions, active states, and the terrain/3D identity.
      // `terrain` is a dedicated near-black scale for the 3D/dark
      // viewport chrome, distinct from `slate` so the "cinematic terrain
      // viewport" reads as an intentional, separate visual register
      // rather than an accidental grey.
      colors: {
        surface: {
          app: "var(--color-surface-app)",
          chrome: "var(--color-surface-chrome)",
          panel: "var(--color-surface-panel)",
          raised: "var(--color-surface-raised)",
          canvas: "var(--color-surface-canvas)",
          data: "var(--color-surface-data)",
        },
        "ui-border": {
          subtle: "var(--color-border-subtle)",
          strong: "var(--color-border-strong)",
        },
        content: {
          primary: "var(--color-text-primary)",
          secondary: "var(--color-text-secondary)",
          muted: "var(--color-text-muted)",
        },
        accent: {
          DEFAULT: "var(--color-accent)",
          hover: "var(--color-accent-hover)",
          active: "var(--color-accent-active)",
        },
        status: {
          info: "var(--color-status-info)",
          success: "var(--color-status-success)",
          warning: "var(--color-status-warning)",
          danger: "var(--color-status-danger)",
        },
        brand: {
          50: "#effefb",
          100: "#c8fdf1",
          200: "#92fae3",
          300: "#57efd4",
          400: "#26dac0",
          500: "#0dbfa8",
          600: "#06998a",
          700: "#0a7a70",
          800: "#0d615a",
          900: "#0f504b",
          950: "#032e2c",
        },
        terrain: {
          800: "#141a1f",
          900: "#0b0f13",
          950: "#05070a",
        },
      },
      fontFamily: {
        sans: [
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      fontSize: {
        metadata: ["0.75rem", { lineHeight: "1rem" }],
        supporting: ["0.8125rem", { lineHeight: "1.125rem" }],
        control: ["0.875rem", { lineHeight: "1.25rem" }],
        "panel-title": ["0.875rem", { lineHeight: "1.25rem", fontWeight: "600" }],
        "workspace-title": ["1.125rem", { lineHeight: "1.5rem", fontWeight: "600" }],
        "page-title": ["1.5rem", { lineHeight: "2rem", fontWeight: "600" }],
      },
      spacing: {
        control: "2.25rem",
        touch: "2.75rem",
        gutter: "clamp(1rem, 2vw, 1.5rem)",
      },
      minHeight: {
        control: "2.25rem",
        touch: "2.75rem",
      },
      minWidth: {
        control: "2.25rem",
        touch: "2.75rem",
      },
      borderRadius: {
        control: "0.25rem",
        panel: "0.375rem",
        dialog: "0.5rem",
      },
      boxShadow: {
        panel: "0 1px 2px 0 rgb(15 23 42 / 0.04), 0 1px 3px 0 rgb(15 23 42 / 0.06)",
        elevated: "0 4px 12px -2px rgb(15 23 42 / 0.10), 0 2px 4px -2px rgb(15 23 42 / 0.06)",
        popover: "0 12px 28px -8px rgb(0 0 0 / 0.35)",
      },
      transitionDuration: {
        fast: "120ms",
        selection: "150ms",
        panel: "220ms",
      },
      transitionTimingFunction: {
        standard: "cubic-bezier(0.2, 0, 0, 1)",
        enter: "cubic-bezier(0.16, 1, 0.3, 1)",
        exit: "cubic-bezier(0.4, 0, 1, 1)",
      },
      keyframes: {
        "fade-in": { from: { opacity: 0 }, to: { opacity: 1 } },
        "fade-in-up": {
          from: { opacity: 0, transform: "translateY(6px)" },
          to: { opacity: 1, transform: "translateY(0)" },
        },
        "scale-in": {
          from: { opacity: 0, transform: "scale(0.97)" },
          to: { opacity: 1, transform: "scale(1)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.25s ease-out both",
        "fade-in-up": "fade-in-up 0.35s cubic-bezier(0.16,1,0.3,1) both",
        "scale-in": "scale-in 0.2s cubic-bezier(0.16,1,0.3,1) both",
        shimmer: "shimmer 1.6s linear infinite",
      },
    },
  },
  plugins: [],
};
