import {
  Alert, Badge, Button, Card, Checkbox, Code, createTheme, defaultVariantColorsResolver, Drawer, Input, Loader, Menu, Modal,
  Paper, parseThemeColor, Popover, rem, rgba, Skeleton, Table, Timeline, Tooltip,
  type CSSVariablesResolver, type MantineColorsTuple, type VariantColorsResolver,
} from '@mantine/core'

import classes from './theme.module.css'

// Warm neutrals for every surface, border and secondary text; `ink` is the same family as the primary
// colour, so buttons, focus rings and checkboxes stay monochrome and status hues carry all the meaning.
const gray: MantineColorsTuple = ['#f7f7f5', '#f0f0ed', '#e6e6e2', '#d6d6d1', '#b4b4ae', '#8a8a84', '#6b6b65', '#4d4d49', '#33332f', '#1f1f1c']
const ink: MantineColorsTuple = ['#f7f7f5', '#eaeae7', '#d6d6d1', '#b4b4ae', '#8a8a84', '#6b6b65', '#4d4d49', '#33332f', '#262623', '#1f1f1c']
const dark: MantineColorsTuple = ['#d7d9de', '#b1b5bd', '#8b909b', '#656b78', '#3d4350', '#2f3440', '#262b36', '#1e222b', '#171a21', '#101217']
const accent: MantineColorsTuple = ['#eef3fb', '#dbe5f6', '#b9cbec', '#93afe0', '#6f94d3', '#5580c8', '#4670bd', '#3a5fa6', '#2f4e89', '#243d6b']

/** `light` = a pale tint with dark text of the same hue (badges, alerts), instead of Mantine's mid-tone text. */
const variantColorResolver: VariantColorsResolver = (input) => {
  const parsed = parseThemeColor({ color: input.color || input.theme.primaryColor, theme: input.theme })
  if (input.variant !== 'light' || !parsed.isThemeColor || parsed.shade !== undefined) return defaultVariantColorsResolver(input)
  const c = parsed.color
  const bg = c === 'gray' || c === 'ink' ? 1 : 0 // the neutrals' shade 0 is the page background
  const mid = input.theme.colors[c][6]
  return {
    background: `light-dark(var(--mantine-color-${c}-${bg}), ${rgba(mid, 0.16)})`,
    hover: `light-dark(var(--mantine-color-${c}-${bg + 1}), ${rgba(mid, 0.24)})`,
    color: `light-dark(var(--mantine-color-${c}-9), var(--mantine-color-${c}-2))`,
    border: `${rem(1)} solid transparent`,
  }
}

export const cssVariablesResolver: CSSVariablesResolver = (theme) => ({
  variables: {},
  light: {
    '--mantine-color-anchor': theme.colors.accent[7],
    '--mantine-color-default-border': theme.colors.gray[3],
    '--mantine-color-default-hover': theme.colors.gray[0],
    '--mantine-color-placeholder': theme.colors.gray[5],
  },
  dark: {
    '--mantine-color-anchor': theme.colors.accent[3],
    '--mantine-color-default-border': theme.colors.dark[4],
  },
})

const sans = '-apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, "Helvetica Neue", Arial, sans-serif'

export const theme = createTheme({
  fontFamily: sans,
  fontFamilyMonospace: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
  fontSizes: { xs: rem(12), sm: rem(13), md: rem(14), lg: rem(16), xl: rem(18) },
  lineHeights: { xs: '1.4', sm: '1.45', md: '1.5', lg: '1.55', xl: '1.6' },
  headings: {
    fontFamily: sans,
    fontWeight: '600',
    sizes: {
      h1: { fontSize: rem(20), lineHeight: '1.3' }, // page title
      h2: { fontSize: rem(14), lineHeight: '1.4' }, // section or panel title
      h3: { fontSize: rem(13), lineHeight: '1.4' },
      h4: { fontSize: rem(13), lineHeight: '1.4' },
      h5: { fontSize: rem(12), lineHeight: '1.4' },
      h6: { fontSize: rem(12), lineHeight: '1.4' },
    },
  },
  colors: { gray, ink, dark, accent },
  primaryColor: 'ink',
  primaryShade: { light: 9, dark: 1 },
  autoContrast: true,
  luminanceThreshold: 0.4,
  black: '#1f1f1c',
  variantColorResolver,
  defaultRadius: 'md',
  radius: { xs: rem(2), sm: rem(4), md: rem(6), lg: rem(8), xl: rem(12) },
  spacing: { xs: rem(8), sm: rem(12), md: rem(16), lg: rem(24), xl: rem(32) },
  shadows: {
    xs: '0 1px 2px rgba(0, 0, 0, 0.04)',
    sm: '0 1px 3px rgba(0, 0, 0, 0.06)',
    md: '0 4px 12px rgba(0, 0, 0, 0.08)',
    lg: '0 8px 24px rgba(0, 0, 0, 0.10)',
    xl: '0 16px 40px rgba(0, 0, 0, 0.14)',
  },
  cursorType: 'pointer',
  components: {
    Alert: Alert.extend({ defaultProps: { radius: 'md' }, classNames: { title: classes.alertTitle, message: classes.alertMessage } }),
    Badge: Badge.extend({
      defaultProps: { size: 'sm', radius: 'sm', variant: 'light' },
      classNames: { root: classes.badge },
      vars: (_, props) => ({
        root: props.size === 'xs'
          ? { '--badge-height': rem(18), '--badge-fz': rem(11), '--badge-padding-x': rem(5) }
          : { '--badge-height': rem(20), '--badge-fz': rem(12), '--badge-padding-x': rem(6) },
      }),
    }),
    Button: Button.extend({
      classNames: { root: classes.button },
      vars: (_, props) => ({ root: props.size === 'xs' ? { '--button-fz': 'var(--mantine-font-size-sm)' } : {} }),
    }),
    Card: Card.extend({ defaultProps: { withBorder: true, radius: 'lg', padding: 'md', shadow: 'none' } }),
    Checkbox: Checkbox.extend({ defaultProps: { radius: 'sm' } }),
    Code: Code.extend({ classNames: { root: classes.code } }),
    Drawer: Drawer.extend({
      defaultProps: { padding: 'lg', overlayProps: { backgroundOpacity: 0.2 } },
      classNames: { title: classes.dialogTitle, header: classes.drawerHeader },
    }),
    Input: Input.extend({ vars: (_, props) => ({ wrapper: props.size === 'xs' ? { '--input-fz': 'var(--mantine-font-size-sm)' } : {} }) }),
    Loader: Loader.extend({ defaultProps: { color: 'gray.5', size: 'sm' } }),
    Menu: Menu.extend({ defaultProps: { shadow: 'lg', radius: 'lg' }, classNames: { dropdown: classes.raised } }),
    Modal: Modal.extend({
      defaultProps: { radius: 'lg', padding: 'lg', overlayProps: { backgroundOpacity: 0.2 } },
      classNames: { title: classes.dialogTitle },
    }),
    Paper: Paper.extend({ defaultProps: { radius: 'lg' } }),
    Popover: Popover.extend({ defaultProps: { shadow: 'lg', radius: 'lg' }, classNames: { dropdown: classes.raised } }),
    Skeleton: Skeleton.extend({ defaultProps: { radius: 'sm' } }),
    Table: Table.extend({
      defaultProps: { verticalSpacing: 8, horizontalSpacing: 10 },
      classNames: { th: classes.th, thead: classes.thead },
      vars: () => ({
        table: {
          '--table-border-color': 'light-dark(var(--mantine-color-gray-2), var(--mantine-color-dark-5))',
          '--table-hover-color': 'light-dark(var(--mantine-color-gray-0), var(--mantine-color-dark-6))',
        },
      }),
    }),
    Timeline: Timeline.extend({ defaultProps: { bulletSize: 10, lineWidth: 1, color: 'gray.4' }, classNames: { itemTitle: classes.timelineTitle } }),
    Tooltip: Tooltip.extend({ defaultProps: { radius: 'md', fz: 'xs' } }),
  },
})
