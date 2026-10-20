import { ActionIcon, Alert, Badge, Button, Checkbox, Group, Menu, Modal, Stack, TextInput, Title } from '@mantine/core'
import { modals } from '@mantine/modals'
import { notifications } from '@mantine/notifications'
import { IconArrowDown, IconArrowUp, IconCopy, IconDots, IconPencil, IconStar, IconStarOff, IconTrash } from '@tabler/icons-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { api, type Page, type SavedView, type ViewState } from '../api'
import { stateUrl, useMeta, useRefreshViews } from '../hooks'

const TITLES: Record<Page, string> = { listings: 'Listings', units: 'Units', events: 'Events' }

function fail(e: unknown) {
  notifications.show({ color: 'red', title: 'Could not save the view', message: String((e as Error).message ?? e) })
}

/** The page title, or the open saved view with its actions. Views are only ever created by the user. */
export function ViewBar({ page, state, view, modified }: { page: Page; state: ViewState; view: SavedView | null; modified: boolean }) {
  const meta = useMeta().data
  const refresh = useRefreshViews()
  const navigate = useNavigate()
  const [naming, setNaming] = useState<null | 'new' | 'rename'>(null)
  const [name, setName] = useState('')
  const [isDefault, setDefault] = useState(false)

  const go = (v: SavedView) => navigate(stateUrl(page, v.state, meta, v.id))
  const run = async (fn: () => Promise<SavedView | void>, after?: (v: SavedView | void) => void) => {
    try { const v = await fn(); await refresh(); after?.(v) } catch (e) { fail(e) }
  }
  const submit = () => {
    if (naming === 'new') {
      run(() => api.post<SavedView>('/api/views', { page, name, is_default: isDefault, state }), (v) => v && go(v))
    } else if (view) {
      run(() => api.patch<SavedView>(`/api/views/${view.id}`, { name }))
    }
    setNaming(null)
  }
  const openName = (kind: 'new' | 'rename') => { setName(kind === 'rename' && view ? view.name : ''); setDefault(false); setNaming(kind) }

  return (
    <Stack gap="xs">
      <Group justify="space-between" wrap="nowrap">
        <Group gap="xs" wrap="nowrap">
          <Title order={3}>{view ? view.name : TITLES[page]}</Title>
          {view?.is_default && <Badge variant="light">default</Badge>}
          {modified && <Badge color="orange" variant="light">modified</Badge>}
        </Group>
        <Group gap="xs" wrap="nowrap">
          {view && modified && (
            <>
              <Button size="xs" variant="subtle" onClick={() => go(view)}>Discard</Button>
              <Button size="xs" onClick={() => run(() => api.patch<SavedView>(`/api/views/${view.id}`, { state }))}>Save changes</Button>
            </>
          )}
          <Button size="xs" variant={view && modified ? 'default' : 'filled'} onClick={() => openName('new')}>
            {view ? 'Save as new view' : 'Save view'}
          </Button>
          {view && (
            <Menu position="bottom-end" withinPortal>
              <Menu.Target><ActionIcon variant="default" aria-label="View actions"><IconDots size={16} /></ActionIcon></Menu.Target>
              <Menu.Dropdown>
                <Menu.Item leftSection={<IconPencil size={14} />} onClick={() => openName('rename')}>Rename</Menu.Item>
                <Menu.Item leftSection={view.is_default ? <IconStarOff size={14} /> : <IconStar size={14} />}
                  onClick={() => run(() => api.patch(`/api/views/${view.id}`, { is_default: !view.is_default }))}>
                  {view.is_default ? 'Stop opening by default' : 'Open by default'}
                </Menu.Item>
                <Menu.Item leftSection={<IconCopy size={14} />}
                  onClick={() => run(() => api.post<SavedView>(`/api/views/${view.id}/duplicate`), (v) => v && go(v))}>Duplicate</Menu.Item>
                <Menu.Item leftSection={<IconArrowUp size={14} />} onClick={() => run(() => api.patch(`/api/views/${view.id}`, { move: -1 }))}>Move up</Menu.Item>
                <Menu.Item leftSection={<IconArrowDown size={14} />} onClick={() => run(() => api.patch(`/api/views/${view.id}`, { move: 1 }))}>Move down</Menu.Item>
                <Menu.Divider />
                <Menu.Item color="red" leftSection={<IconTrash size={14} />} onClick={() => modals.openConfirmModal({
                  title: `Delete “${view.name}”?`, labels: { confirm: 'Delete', cancel: 'Cancel' }, confirmProps: { color: 'red' },
                  onConfirm: () => run(() => api.delete(`/api/views/${view.id}`), () => navigate(stateUrl(page, view.state, meta))),
                })}>Delete</Menu.Item>
              </Menu.Dropdown>
            </Menu>
          )}
        </Group>
      </Group>
      {view?.warnings.map((w) => <Alert key={w} color="orange" variant="light">{w}</Alert>)}
      <Modal opened={naming !== null} onClose={() => setNaming(null)} title={naming === 'new' ? 'Save view' : 'Rename view'}>
        <form onSubmit={(e) => { e.preventDefault(); submit() }}>
          <Stack>
            <TextInput label="Name" value={name} onChange={(e) => setName(e.currentTarget.value)} data-autofocus maxLength={80} required />
            {naming === 'new' && <Checkbox label={`Open by default on ${TITLES[page]}`} checked={isDefault} onChange={(e) => setDefault(e.currentTarget.checked)} />}
            <Group justify="flex-end"><Button type="submit" disabled={!name.trim()}>Save</Button></Group>
          </Stack>
        </form>
      </Modal>
    </Stack>
  )
}
