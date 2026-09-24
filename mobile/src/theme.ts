// Natya mobile design system -- single source of truth for color, type,
// spacing and radius so every screen looks like one product instead of
// each screen inventing its own shade of "dark".
//
// Accent stays the existing Natya brand yellow (#facc15) already used by
// the web app, the admin dashboard and this app's own icon/splash --
// changing the hue would fragment brand identity across surfaces. What
// changes here is discipline of USE: previously nearly every badge, link
// and label reached for the accent by default. In this system the accent
// is reserved for the primary action, the active nav/tab state, selection
// state and progress fill -- never body text, never a background fill,
// never decoration.

export const colors = {
  // Backgrounds
  bg: '#050505',
  bgElevated: '#0D0D0D',
  card: '#141414',
  cardAlt: '#181818',
  overlay: 'rgba(5,5,5,0.72)',
  scrim: 'rgba(0,0,0,0.55)',

  // Text
  text: '#F4F4F5',
  textSecondary: '#A1A1AA',
  textTertiary: '#71717A',
  textInverse: '#0A0A0A',

  // Accent -- restrained, see module docstring
  accent: '#F5C518',
  accentMuted: 'rgba(245, 197, 24, 0.14)',
  accentBorder: 'rgba(245, 197, 24, 0.35)',

  // Semantic
  success: '#22C55E',
  danger: '#F87171',
  info: '#60A5FA',
  warning: '#FB923C',

  // Structure
  border: 'rgba(255,255,255,0.08)',
  borderStrong: 'rgba(255,255,255,0.14)',
  divider: 'rgba(255,255,255,0.06)',
};

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  xxl: 24,
  xxxl: 32,
};

export const radius = {
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  pill: 999,
};

// Type scale: hero (screen titles) > section (rail/section headers) >
// body (course/lesson titles) > meta (secondary/metadata). Weights stay
// modest (600 max) -- the brief explicitly calls out "avoid excessive
// bold text".
export const typography = {
  hero: { fontSize: 26, fontWeight: '700' as const, color: colors.text, lineHeight: 32 },
  title: { fontSize: 20, fontWeight: '600' as const, color: colors.text, lineHeight: 26 },
  section: { fontSize: 15, fontWeight: '600' as const, color: colors.text, lineHeight: 20 },
  body: { fontSize: 15, fontWeight: '500' as const, color: colors.text, lineHeight: 21 },
  bodyRegular: { fontSize: 14, fontWeight: '400' as const, color: colors.textSecondary, lineHeight: 20 },
  meta: { fontSize: 12, fontWeight: '500' as const, color: colors.textSecondary, lineHeight: 16 },
  caption: { fontSize: 11, fontWeight: '600' as const, color: colors.textTertiary, letterSpacing: 0.4, lineHeight: 14 },
};

export const layout = {
  screenPadding: spacing.lg,
  cardImageAspect: 16 / 9,
};
