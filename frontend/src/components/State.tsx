import { Button, Group, Paper, Skeleton, Stack, Text, VisuallyHidden } from '@mantine/core'
import { IconAlertCircle, IconRefresh } from '@tabler/icons-react'

// the widths of one skeleton row, shaped like a list row: a title cell, then a few short values
const ROW = ['28%', '8%', '10%', '8%', '12%']

/** A page or panel still loading: row-shaped skeletons in the content's place, announced as "Loading…". */
export function Loading({ rows = 6, label = 'Loading…' }: { rows?: number; label?: string }) {
  return (
    <Stack gap="md" role="status" aria-live="polite">
      <VisuallyHidden>{label}</VisuallyHidden>
      <Skeleton h={22} w={160} mb="xs" />
      {Array.from({ length: rows }, (_, i) => (
        <Group key={i} gap="lg" wrap="nowrap">{ROW.map((w, j) => <Skeleton key={j} h={10} w={w} />)}</Group>
      ))}
    </Stack>
  )
}

/** A failed request: what failed, the server's reason, and a way to try again. */
export function LoadError({ what, error, retry }: { what: string; error: unknown; retry?: () => void }) {
  const reason = error instanceof Error ? error.message : String(error)
  return (
    <Paper withBorder p="md" role="alert" maw={560}>
      <Group align="flex-start" gap="sm" wrap="nowrap">
        <IconAlertCircle size={18} color="var(--mantine-color-red-7)" style={{ flex: 'none', marginTop: 1 }} aria-hidden />
        <Stack gap={4}>
          <Text size="sm" fw={600}>Couldn’t load {what}</Text>
          <Text size="xs" c="dimmed" ff="monospace" translate="no">{reason}</Text>
          <Text size="sm" c="dimmed">Check that the housing-watch server is running and reachable, then try again.</Text>
          {retry && (
            <Group mt="xs">
              <Button size="xs" variant="default" leftSection={<IconRefresh size={14} aria-hidden />} onClick={retry}>Try Again</Button>
            </Group>
          )}
        </Stack>
      </Group>
    </Paper>
  )
}
