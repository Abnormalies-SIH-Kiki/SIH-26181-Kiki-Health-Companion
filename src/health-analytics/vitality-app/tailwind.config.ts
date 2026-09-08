import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        "secondary-fixed-dim": "#c8c6c8",
        "tertiary-container": "#0c0b09",
        "error-container": "#93000a",
        "surface-container-highest": "#353434",
        "tertiary": "#cac6c2",
        "primary-container": "#0b0b0c",
        "tertiary-fixed-dim": "#cac6c2",
        "on-primary-container": "#7b797a",
        "surface-container-high": "#2b2a2a",
        "surface-container-lowest": "#0e0e0e",
        "inverse-primary": "#5f5e5f",
        "inverse-on-surface": "#313030",
        "surface": "#141313",
        "on-surface-variant": "#c7c6ca",
        "glow-white": "rgba(255, 255, 255, 0.08)",
        "on-primary-fixed": "#1c1b1c",
        "primary-fixed-dim": "#c8c6c7",
        "surface-tint": "#c8c6c7",
        "calorie-green": "#22C55E",
        "surface-variant": "#353434",
        "on-secondary-fixed": "#1b1b1d",
        "error": "#ffb4ab",
        "secondary-container": "#474649",
        "on-error": "#690005",
        "on-tertiary-fixed-variant": "#494644",
        "surface-tertiary": "#1C1C1E",
        "heart-red": "#EF4444",
        "outline-variant": "#46464a",
        "on-background": "#e5e2e1",
        "secondary-fixed": "#e4e2e4",
        "on-surface": "#e5e2e1",
        "surface-dim": "#141313",
        "on-tertiary": "#32302e",
        "inverse-surface": "#e5e2e1",
        "on-secondary-fixed-variant": "#474649",
        "outline": "#919094",
        "border-subtle": "rgba(255, 255, 255, 0.05)",
        "on-tertiary-container": "#7c7976",
        "on-secondary-container": "#b7b4b7",
        "surface-bright": "#3a3939",
        "tertiary-fixed": "#e7e2dd",
        "surface-container-low": "#1c1b1b",
        "on-primary-fixed-variant": "#474647",
        "on-primary": "#313031",
        "on-error-container": "#ffdad6",
        "on-tertiary-fixed": "#1d1b19",
        "step-blue": "#3B82F6",
        "primary-fixed": "#e5e2e3",
        "background": "#141313",
        "secondary": "#c8c6c8",
        "on-secondary": "#303032",
        "primary": "#c8c6c7",
        "surface-container": "#201f1f"
      },
      borderRadius: {
        DEFAULT: "0.25rem",
        lg: "0.5rem",
        xl: "0.75rem",
        full: "9999px"
      },
      spacing: {
        "section-gap": "3rem",
        "inner-padding": "1.25rem",
        "gutter": "1rem",
        "group-gap": "1.5rem",
        "metric-inset": "2rem"
      },
      fontFamily: {
        "label-caps": ["Hanken Grotesk"],
        "headline-lg": ["Hanken Grotesk"],
        "display-metric": ["Hanken Grotesk"],
        "headline-sm": ["Hanken Grotesk"],
        "body-md": ["Inter"],
        "display-metric-mobile": ["Hanken Grotesk"],
        "data-point": ["Hanken Grotesk"],
        "body-lg": ["Inter"]
      },
      fontSize: {
        "label-caps": ["12px", { lineHeight: "16px", letterSpacing: "0.1em", fontWeight: "700" }],
        "headline-lg": ["32px", { lineHeight: "40px", letterSpacing: "-0.02em", fontWeight: "600" }],
        "display-metric": ["64px", { lineHeight: "72px", letterSpacing: "-0.04em", fontWeight: "800" }],
        "headline-sm": ["20px", { lineHeight: "28px", letterSpacing: "-0.01em", fontWeight: "600" }],
        "body-md": ["16px", { lineHeight: "24px", fontWeight: "400" }],
        "display-metric-mobile": ["48px", { lineHeight: "52px", letterSpacing: "-0.03em", fontWeight: "800" }],
        "data-point": ["24px", { lineHeight: "32px", fontWeight: "500" }],
        "body-lg": ["18px", { lineHeight: "28px", fontWeight: "400" }]
      }
    }
  },
  plugins: [],
};
export default config;
