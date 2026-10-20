import { Alert, Button, Group, Skeleton, Stack, Text, VisuallyHidden } from '@mantine/core'
import { IconRefresh } from '@tabler/icons-react'

/** A page or panel still loading: skeleton lines in the content's place, announced as "Loading…". */
export function Loading({ rows = 6, label = 'Loading…' }: { rows?: number; label?: string }) {
  return (
    <Stack gap="xs" role="status" aria-live="polite">
      <VisuallyHidden>{label}</VisuallyHidden>
      <Skeleton h={28} w="40%" />
      {Array.from({ length: rows }, (_, i) => <Skeleton key={i} h={18} />)}
    </Stack>
  )
}

/** A failed request: what failed, the server's reason, and a way to try again. */
export function LoadError({ what, error, retry }: { what: string; error: unknown; retry?: () => void }) {
  const reason = error instanceof Error ? error.message : String(error)
  return (
    <Alert color="red" variant="light" title={`Couldn’t load ${what}`} role="alert">
      <Stack gap="xs">
        <Text size="sm" translate="no">{reason}</Text>
        <Text size="sm">Check that the housing-watch server is running and reachable, then try again.</Text>
        {retry && (
          <Group>
            <Button size="xs" variant="default" leftSection={<IconRefresh size={14} aria-hidden />} onClick={retry}>Try Again</Button>
          </Group>
        )}
      </Stack>
    </Alert>
  )
}
