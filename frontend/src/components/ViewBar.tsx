import { ActionIcon, Alert, Badge, Button, Checkbox, Group, Menu, Modal, Stack, TextInput, Title } from '@mantine/core'
import { modals } from '@mantine/modals'
import { notifications } from '@mantine/notifications'
import { IconAlertTriangle, IconArrowDown, IconArrowUp, IconBookmark, IconCopy, IconDots, IconPencil, IconStar, IconStarOff, IconTrash } from '@tabler/icons-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { api, type Page, type SavedView, type ViewState } from '../api'
import { stateUrl, TITLES, useMeta, useMotion, useRefreshViews } from '../hooks'

const message = (e: unknown) => String((e as Error)?.message ?? e)

/** The page title, or the open saved view with its actions. Views are only ever created by the user. */
export function ViewBar({ page, state, view, modified }: { page: Page; state: ViewState; view: SavedView | null; modified: boolean }) {
  const meta = useMeta().data
  const refresh = useRefreshViews()
  const navigate = useNavigate()
  const motion = useMotion()
  const [naming, setNaming] = useState<null | 'new' | 'rename'>(null)
  const [name, setName] = useState('')
  const [isDefault, setDefault] = useState(false)
  const [nameError, setNameError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null) // the action in flight, for its button's spinner

  const go = (v: SavedView) => navigate(stateUrl(page, v.state, meta, v.id))
  /** Runs a view change; says what happened either way. Returns false when it failed. */
  const run = async (what: string, done: string, fn: () => Promise<SavedView | void>, after?: (v: SavedView | void) => void) => {
    setBusy(what)
    try {
      const v = await fn()
      await refresh()
      after?.(v)
      notifications.show({ color: 'green', message: done })
      return true
    } catch (e) {
      notifications.show({ color: 'red', title: `Couldn’t ${what}`, message: `${message(e)} Try again; your filters are still in the address bar.` })
      return false
    } finally {
      setBusy(null)
    }
  }
  const submit = async () => {
    const n = name.trim()
    if (!n) { setNameError('Enter a name for the view.'); return }
    setBusy('save the view')
    try {
      const v = naming === 'new'
        ? await api.post<SavedView>('/api/views', { page, name: n, is_default: isDefault, state })
        : await api.patch<SavedView>(`/api/views/${view!.id}`, { name: n })
      await refresh()
      setNaming(null)
      notifications.show({ color: 'green', message: naming === 'new' ? `Saved “${n}”` : `Renamed to “${n}”` })
      if (naming === 'new') go(v)
    } catch (e) {
      setNameError(message(e)) // e.g. a duplicate name: shown on the field, the dialog stays open
    } finally {
      setBusy(null)
    }
  }
  const openName = (kind: 'new' | 'rename') => {
    setName(kind === 'rename' && view ? view.name : ''); setDefault(false); setNameError(null); setNaming(kind)
  }

  return (
    <Stack gap="xs">
      <Group justify="space-between" gap="sm">
        <Group gap="xs" miw={0}>
          <Title order={1} textWrap="balance">{view ? view.name : TITLES[page]}</Title>
          {view?.is_default && <Badge variant="default" c="dimmed">Default</Badge>}
          {modified && <Badge color="orange">Modified</Badge>}
        </Group>
        <Group gap="xs">
          {view && modified && <Button size="xs" variant="subtle" c="dimmed" onClick={() => go(view)}>Discard Changes</Button>}
          <Button size="xs" variant="default" leftSection={<IconBookmark size={15} aria-hidden />} onClick={() => openName('new')}>
            {view ? 'Save as New View' : 'Save View'}
          </Button>
          {view && modified && (
            <Button size="xs" loading={busy === 'save the changes'}
              onClick={() => run('save the changes', `Saved changes to “${view.name}”`, () => api.patch<SavedView>(`/api/views/${view.id}`, { state }))}>
              Save Changes
            </Button>
          )}
          {view && (
            <Menu position="bottom-end" withinPortal transitionProps={motion}>
              <Menu.Target>
                <ActionIcon variant="default" size="input-xs" aria-label={`Actions for view “${view.name}”`} loading={Boolean(busy && busy !== 'save the changes' && busy !== 'save the view')}>
                  <IconDots size={16} aria-hidden />
                </ActionIcon>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Item leftSection={<IconPencil size={15} aria-hidden />} onClick={() => openName('rename')}>Rename…</Menu.Item>
                <Menu.Item leftSection={view.is_default ? <IconStarOff size={15} aria-hidden /> : <IconStar size={15} aria-hidden />}
                  onClick={() => run('change the default view', view.is_default ? `“${view.name}” no longer opens by default` : `“${view.name}” now opens by default`,
                    () => api.patch(`/api/views/${view.id}`, { is_default: !view.is_default }))}>
                  {view.is_default ? 'Stop Opening by Default' : 'Open by Default'}
                </Menu.Item>
                <Menu.Item leftSection={<IconCopy size={15} aria-hidden />}
                  onClick={() => run('duplicate the view', `Duplicated “${view.name}”`, () => api.post<SavedView>(`/api/views/${view.id}/duplicate`), (v) => v && go(v))}>
                  Duplicate
                </Menu.Item>
                <Menu.Item leftSection={<IconArrowUp size={15} aria-hidden />}
                  onClick={() => run('move the view', `Moved “${view.name}” up`, () => api.patch(`/api/views/${view.id}`, { move: -1 }))}>Move Up</Menu.Item>
                <Menu.Item leftSection={<IconArrowDown size={15} aria-hidden />}
                  onClick={() => run('move the view', `Moved “${view.name}” down`, () => api.patch(`/api/views/${view.id}`, { move: 1 }))}>Move Down</Menu.Item>
                <Menu.Divider />
                <Menu.Item color="red" leftSection={<IconTrash size={15} aria-hidden />} onClick={() => modals.openConfirmModal({
                  title: `Delete “${view.name}”?`, children: 'The view’s filters, sort and columns are removed. Listings are not affected.',
                  labels: { confirm: 'Delete View', cancel: 'Keep View' }, confirmProps: { color: 'red' }, cancelProps: { variant: 'default' },
                  transitionProps: motion,
                  onConfirm: () => run('delete the view', `Deleted “${view.name}”`, () => api.delete(`/api/views/${view.id}`), () => navigate(stateUrl(page, view.state, meta))),
                })}>Delete…</Menu.Item>
              </Menu.Dropdown>
            </Menu>
          )}
        </Group>
      </Group>
      {view?.warnings.map((w) => <Alert key={w} color="orange" variant="light" icon={<IconAlertTriangle size={16} aria-hidden />}>{w}</Alert>)}
      <Modal opened={naming !== null} onClose={() => setNaming(null)} title={naming === 'new' ? 'Save View' : 'Rename View'} transitionProps={motion}>
        <form onSubmit={(e) => { e.preventDefault(); submit() }}>
          <Stack>
            <TextInput label="Name" name="view-name" autoComplete="off" placeholder="e.g. Brooklyn studios under $1,500…" value={name}
              onChange={(e) => { setName(e.currentTarget.value); setNameError(null) }} error={nameError} data-autofocus maxLength={80} required />
            {naming === 'new' && <Checkbox label={`Open by default on ${TITLES[page]}`} checked={isDefault} onChange={(e) => setDefault(e.currentTarget.checked)} />}
            <Group justify="flex-end" gap="xs">
              <Button variant="default" onClick={() => setNaming(null)}>Cancel</Button>
              <Button type="submit" loading={busy === 'save the view'}>{naming === 'new' ? 'Save View' : 'Rename View'}</Button>
            </Group>
          </Stack>
        </form>
      </Modal>
    </Stack>
  )
}
