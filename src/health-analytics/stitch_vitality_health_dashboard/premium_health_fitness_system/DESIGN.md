---
name: Premium Health & Fitness System
colors:
  surface: '#131313'
  surface-dim: '#131313'
  surface-bright: '#393939'
  surface-container-lowest: '#0e0e0e'
  surface-container-low: '#1b1b1b'
  surface-container: '#1f1f1f'
  surface-container-high: '#2a2a2a'
  surface-container-highest: '#353535'
  on-surface: '#e2e2e2'
  on-surface-variant: '#cfc4c5'
  inverse-surface: '#e2e2e2'
  inverse-on-surface: '#303030'
  outline: '#988e90'
  outline-variant: '#4c4546'
  surface-tint: '#c6c6c6'
  primary: '#c6c6c6'
  on-primary: '#303030'
  primary-container: '#000000'
  on-primary-container: '#757575'
  inverse-primary: '#5e5e5e'
  secondary: '#ffb4aa'
  on-secondary: '#690003'
  secondary-container: '#c5020b'
  on-secondary-container: '#ffd2cc'
  tertiary: '#53e16f'
  on-tertiary: '#003911'
  tertiary-container: '#000000'
  on-tertiary-container: '#008833'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#e2e2e2'
  primary-fixed-dim: '#c6c6c6'
  on-primary-fixed: '#1b1b1b'
  on-primary-fixed-variant: '#474747'
  secondary-fixed: '#ffdad5'
  secondary-fixed-dim: '#ffb4aa'
  on-secondary-fixed: '#410001'
  on-secondary-fixed-variant: '#930005'
  tertiary-fixed: '#72fe88'
  tertiary-fixed-dim: '#53e16f'
  on-tertiary-fixed: '#002107'
  on-tertiary-fixed-variant: '#00531c'
  background: '#131313'
  on-background: '#e2e2e2'
  surface-variant: '#353535'
typography:
  display-stat:
    fontFamily: hankenGrotesk
    fontSize: 48px
    fontWeight: '700'
    lineHeight: 56px
    letterSpacing: -0.04em
  headline-lg:
    fontFamily: hankenGrotesk
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-lg-mobile:
    fontFamily: hankenGrotesk
    fontSize: 28px
    fontWeight: '600'
    lineHeight: 34px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: hankenGrotesk
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 30px
    letterSpacing: -0.01em
  body-lg:
    fontFamily: hankenGrotesk
    fontSize: 18px
    fontWeight: '400'
    lineHeight: 28px
    letterSpacing: 0em
  body-md:
    fontFamily: hankenGrotesk
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
    letterSpacing: 0em
  label-caps:
    fontFamily: hankenGrotesk
    fontSize: 12px
    fontWeight: '700'
    lineHeight: 16px
    letterSpacing: 0.08em
  numeric-data:
    fontFamily: hankenGrotesk
    fontSize: 20px
    fontWeight: '500'
    lineHeight: 24px
    letterSpacing: -0.01em
rounded:
  sm: 0.5rem
  DEFAULT: 1rem
  md: 1.5rem
  lg: 2rem
  xl: 3rem
  full: 9999px
spacing:
  container-padding: 20px
  stack-gap: 16px
  section-margin: 32px
  inline-gutter: 12px
---

## Brand & Style

This design system is built on a philosophy of **Atmospheric Minimalism**. It targets a high-end audience that values precision, studio-quality aesthetics, and high-fidelity data visualization. The visual identity is defined by a "Premium Noir" execution: a deep, high-contrast dark mode that allows biometric data and activity metrics to vibrate against a pure black canvas.

The emotional response should be one of "Focused Performance." By stripping away unnecessary UI chrome and relying on sharp, contemporary typography, the interface transforms into a sophisticated dashboard. The style draws heavily from **High-Contrast Minimalism** and **Glassmorphism**, using light as a primary tool for hierarchy rather than physical depth. It is designed to feel like a high-performance tool found in a professional athletic facility or a luxury medical suite.

## Colors

The color strategy utilizes a **Pure Black (#000000)** foundation to maximize the OLED contrast and create an infinite sense of depth. Color is used strictly as a functional indicator of health status and achievement:

*   **Heart Rate / Vitality:** Apple-inspired 'Health Red' (#FF3B30) is used for cardiovascular data and urgent alerts.
*   **Activity / Energy:** 'Fitness Orange' (#FF9500) signifies active calories and movement.
*   **Completion / Success:** 'Activity Green' (#34C759) is reserved for goal attainment and recovery metrics.
*   **Surfaces:** Secondary containers use a deep 'Elevation Gray' (#1C1C1E) to subtly separate content from the background without losing the premium dark-mode aesthetic.

## Typography

Typography is the primary driver of the "High-Fidelity" feel. We use **Hanken Grotesk** across all roles for its sharp, geometric precision and contemporary spirit.

*   **Data Hierarchy:** Large-scale `display-stat` sizes are used for primary metrics (e.g., heart rate, step count). These use a tight negative letter spacing to feel "engineered" and impactful.
*   **Labels:** Small, uppercase labels with increased tracking (letter spacing) are used to categorize data points, providing a technical, studio-quality look.
*   **Scale:** On mobile devices, headlines scale down slightly to maintain readability while preserving the bold, confident weight.

## Layout & Spacing

The layout follows a **Fluid Grid** logic with generous internal padding to reflect a premium sense of space. 

*   **Rhythm:** A consistent 4px baseline grid governs all spacing. The standard padding for data cards is 20px, providing enough "breathing room" for complex health charts.
*   **Mobile:** A single-column stack is preferred on mobile to allow charts and rings to span the full width of the viewport minus the container padding.
*   **Desktop/Tablet:** Content reflows into a 12-column grid where cards typically span 4 or 6 columns depending on the density of the data visualization.

## Elevation & Depth

Depth is conveyed through **Tonal Layering** and **Glassmorphism** rather than traditional drop shadows.

*   **Base Layer:** Pure Black (#000000).
*   **Surface Layer:** Deep Gray (#1C1C1E) used for cards and containers.
*   **Glass Elements:** Fixed elements like the bottom navigation bar and top headers utilize a high-density `backdrop-filter: blur(20px)` with a semi-transparent background (e.g., `rgba(28, 28, 30, 0.8)`).
*   **Interaction:** Active states are indicated by a subtle inner glow or an increase in surface brightness rather than a shadow, maintaining a sleek, flat-but-layered aesthetic.

## Shapes

The design system uses a **Pill-shaped (3)** roundedness strategy to soften the high-contrast visuals and evoke a friendly, approachable feeling within a technical environment.

*   **Primary Cards:** 24px corner radius (`rounded-lg` or `rounded-xl`).
*   **Buttons & Chips:** Fully rounded (pill) ends to differentiate interactive elements from informational containers.
*   **Data Visuals:** Line charts must use smooth cubic Bézier curves. Progress rings should have rounded caps to maintain consistency with the overall shape language.

## Components

*   **Progress Rings:** The core component. Uses a heavy stroke (12-16px) with rounded caps. Primary, secondary, and tertiary colors are nested to show multiple activity goals simultaneously.
*   **Rounded Cards:** Cards use the Surface color (#1C1C1E) and have a 24px radius. They should never have a shadow on the black background; instead, use a 1px subtle border (#2C2C2E) if further separation is needed.
*   **Buttons:** High-contrast execution. Primary buttons are white with black text; secondary buttons are pill-shaped with a glass effect.
*   **Bottom Navigation:** Fixed at the bottom with a 20px blur and 80% opacity. Icons are thin-line weights (1.5pt) that transition to solid fills when active.
*   **Data Charts:** Line charts should feature a subtle gradient fill underneath the line, fading from the accent color (e.g., Activity Green) to transparent.
*   **Health Chips:** Small, pill-shaped indicators used for tagging workout types (e.g., "HIIT", "Yoga"). They use a low-opacity version of the category color with high-contrast text.