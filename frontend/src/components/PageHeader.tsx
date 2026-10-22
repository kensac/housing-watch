import { Group, Stack, Text, Title } from '@mantine/core'
import type { ReactNode } from 'react'

/** A page's title with its one-line description, and room for actions on the right. */
export function PageHeader({ title, description, right }: { title: string; description?: ReactNode; right?: ReactNode }) {
  return (
    <Stack gap={4}>
      <Group justify="space-between" gap="sm"><Title order={1}>{title}</Title>{right}</Group>
      {description && <Text size="sm" c="dimmed">{description}</Text>}
    </Stack>
  )
}
