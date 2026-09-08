---
name: Premium Health-Tech System
colors:
  surface: '#141313'
  surface-dim: '#141313'
  surface-bright: '#3a3939'
  surface-container-lowest: '#0e0e0e'
  surface-container-low: '#1c1b1b'
  surface-container: '#201f1f'
  surface-container-high: '#2b2a2a'
  surface-container-highest: '#353434'
  on-surface: '#e5e2e1'
  on-surface-variant: '#c7c6ca'
  inverse-surface: '#e5e2e1'
  inverse-on-surface: '#313030'
  outline: '#919094'
  outline-variant: '#46464a'
  surface-tint: '#c8c6c7'
  primary: '#c8c6c7'
  on-primary: '#313031'
  primary-container: '#0b0b0c'
  on-primary-container: '#7b797a'
  inverse-primary: '#5f5e5f'
  secondary: '#c8c6c8'
  on-secondary: '#303032'
  secondary-container: '#474649'
  on-secondary-container: '#b7b4b7'
  tertiary: '#cac6c2'
  on-tertiary: '#32302e'
  tertiary-container: '#0c0b09'
  on-tertiary-container: '#7c7976'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#e5e2e3'
  primary-fixed-dim: '#c8c6c7'
  on-primary-fixed: '#1c1b1c'
  on-primary-fixed-variant: '#474647'
  secondary-fixed: '#e4e2e4'
  secondary-fixed-dim: '#c8c6c8'
  on-secondary-fixed: '#1b1b1d'
  on-secondary-fixed-variant: '#474649'
  tertiary-fixed: '#e7e2dd'
  tertiary-fixed-dim: '#cac6c2'
  on-tertiary-fixed: '#1d1b19'
  on-tertiary-fixed-variant: '#494644'
  background: '#141313'
  on-background: '#e5e2e1'
  surface-variant: '#353434'
  surface-tertiary: '#1C1C1E'
  calorie-green: '#22C55E'
  step-blue: '#3B82F6'
  heart-red: '#EF4444'
  border-subtle: rgba(255, 255, 255, 0.05)
  glow-white: rgba(255, 255, 255, 0.08)
typography:
  display-metric:
    fontFamily: Hanken Grotesk
    fontSize: 64px
    fontWeight: '800'
    lineHeight: 72px
    letterSpacing: -0.04em
  display-metric-mobile:
    fontFamily: Hanken Grotesk
    fontSize: 48px
    fontWeight: '800'
    lineHeight: 52px
    letterSpacing: -0.03em
  headline-lg:
    fontFamily: Hanken Grotesk
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-sm:
    fontFamily: Hanken Grotesk
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
    letterSpacing: -0.01em
  body-lg:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '400'
    lineHeight: 28px
  body-md:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  label-caps:
    fontFamily: Hanken Grotesk
    fontSize: 12px
    fontWeight: '700'
    lineHeight: 16px
    letterSpacing: 0.1em
  data-point:
    fontFamily: Hanken Grotesk
    fontSize: 24px
    fontWeight: '500'
    lineHeight: 32px
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  section-gap: 3rem
  group-gap: 1.5rem
  inner-padding: 1.25rem
  metric-inset: 2rem
  gutter: 1rem
---

## Brand & Style

This design system is engineered for the "Premium Noir" health-tech market, targeting elite performance tracking and high-fidelity biometric data. The brand personality is clinical yet sophisticated, combining the precision of a medical instrument with the aesthetic of a luxury studio. 

The visual style is a fusion of **Minimalism** and **Atmospheric Glassmorphism**. It rejects the generic "card-on-canvas" dashboard look in favor of integrated sections and floating metric groups. The emotional response is one of "Deep Focus"—a calm, distraction-free environment where physiological data is the protagonist. The interface should feel expensive, utilizing light, subtle borders, and generous whitespace to convey a sense of high-performance engineering.

## Colors

The palette is built on a "Deep Charcoal" foundation to provide a softer, more sophisticated alternative to pure black while maintaining OLED-grade contrast.

*   **Foundation:** The primary background uses Deep Charcoal (#0B0B0C). Layering is achieved through two elevation steps: Surface (#161618) and Secondary Surface (#1C1C1E).
*   **Functional Accents:** Color is used exclusively for data categorization. Calorie Green, Step Blue, and Heart Red are high-saturation "vibrant" hues that appear to glow against the dark canvas.
*   **Refinement:** Borders are never high-contrast; they use a 5% white opacity to create structural definition without visual noise.

## Typography

Typography acts as a structural element. **Hanken Grotesk** provides a geometric, modern edge for headlines and data, while **Inter** ensures maximum legibility for body text and long-form insights.

*   **Metric Dominance:** The `display-metric` style is designed for large-scale numbers. It uses an extra-bold weight and tight tracking to feel solid and impactful.
*   **Technical Labels:** Captions and metadata use `label-caps` with wide letter-spacing to create a technical, "instrument-cluster" feel.
*   **Hierarchical Weight:** Use dynamic weights in Hanken Grotesk to differentiate between primary data points (Bold) and secondary units (Medium/Regular).

## Layout & Spacing

The layout philosophy emphasizes **Integrated Sections** over modular cards. This creates a cohesive "single surface" feel where content groups are separated by generous whitespace rather than physical borders.

*   **The Grid:** A 12-column grid is used for desktop, but spacing is driven by "Floating Metric Groups"—elements that align to the grid but utilize `metric-inset` to breathe within their logical sections.
*   **Whitespace:** Use `section-gap` (48px) to separate major health domains (e.g., Activity vs. Sleep). This "expensive" use of space prevents the interface from feeling cluttered.
*   **Fluidity:** On mobile, margins are kept tight (16px) to maximize the horizontal scale of charts, while vertical spacing remains generous to facilitate easy scrolling and touch interaction.

## Elevation & Depth

Depth in this system is created through **Tonal Layers** and **Light Injection** rather than shadows.

*   **Surface Hierarchy:** Objects do not "cast shadows" on the background. Instead, they sit on slightly lighter surfaces (#161618) to indicate elevation.
*   **Inner Glows:** To define "floating" groups, use a subtle 1px inner border with a soft `0px 4px 12px rgba(255, 255, 255, 0.03)` glow. This makes elements feel like they are lit from within or slightly illuminated by the screen.
*   **Glassmorphism:** Navigation and persistent overlays utilize a heavy backdrop blur (32px) with a 70% opacity fill of the secondary surface color, creating a lens-like effect over the data beneath.

## Shapes

The shape language is defined by "Human-Centric Geometry." While the system is technical, the use of large radii (24px to 32px) ensures it feels approachable.

*   **Integrated Sections:** Large content areas use a 24px radius (`rounded-lg`). 
*   **Floating Elements:** Small metric groups or "chips" use a 32px radius (`rounded-xl`) or full pill-shaping to distinguish them from structural sections.
*   **Interactive Components:** Buttons and input fields should consistently use the `rounded-lg` (16px in this system) or pill-shape to provide a clear tactile affordance.

## Components

*   **Floating Metric Groups:** These are the primary data containers. They feature a 1px solid `rgba(255,255,255,0.05)` border, a `secondary-surface` background, and no external shadows. The focus is on the large `display-metric` number centered within.
*   **Progress Rings:** Thick 12px strokes with rounded caps. The "track" of the ring should be `primary-color` (the background) with a very subtle inner glow, making the colored "progress" portion appear to sit inside a recessed groove.
*   **Buttons:** 
    *   *Primary:* Solid white with #0B0B0C text for maximum contrast.
    *   *Secondary:* Ghost style with the 1px subtle border and a blur background.
*   **Health Chips:** Small, fully rounded indicators for status (e.g., "Active", "Recovering"). Use a 10% opacity version of the accent color (Green/Blue/Red) with the full-strength accent color for the text.
*   **Input Fields:** Minimalist design with only a bottom 1px border that glows slightly (Step Blue) when focused.
*   **Charts:** Line graphs should use "Smoothing" (Cubic Bézier) and a vertical gradient fill that transitions from the accent color at the top to transparent at the baseline. Avoid grid lines; use only essential X/Y labels in `label-caps`.