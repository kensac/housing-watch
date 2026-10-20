import { AppShell, Badge, Burger, Button, Group, NavLink, ScrollArea, Text } from '@mantine/core'
import { useDisclosure, useReducedMotion } from '@mantine/hooks'
import { IconActivity, IconBuilding, IconDoor, IconHome, IconList, IconServer, IconTimeline } from '@tabler/icons-react'
import { useState } from 'react'
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom'

import type { Page } from './api'
import { num } from './format'
import { stateUrl, useMeta, useViews } from './hooks'
import { EventsPage } from './pages/Events'
import { FleetPage } from './pages/Fleet'
import { ListingsPage } from './pages/Listings'
import { OverviewPage } from './pages/Overview'
import { SourcesPage } from './pages/Sources'
import { UnitsPage } from './pages/Units'

const PAGES: { to: string; label: string; icon: React.ReactNode; page?: Page }[] = [
  { to: '/', label: 'Overview', icon: <IconHome size={16} aria-hidden /> },
  { to: '/listings', label: 'Listings', icon: <IconBuilding size={16} aria-hidden />, page: 'listings' },
  { to: '/units', label: 'Units', icon: <IconDoor size={16} aria-hidden />, page: 'units' },
  { to: '/events', label: 'Events', icon: <IconTimeline size={16} aria-hidden />, page: 'events' },
  { to: '/sources', label: 'Sources', icon: <IconList size={16} aria-hidden /> },
  { to: '/fleet', label: 'Fleet', icon: <IconServer size={16} aria-hidden /> },
]

function Nav({ onNavigate }: { onNavigate: () => void }) {
  const loc = useLocation()
  const meta = useMeta().data
  const views = useViews().data ?? []
  const viewId = new URLSearchParams(loc.search).get('view')
  return (
    <>
      {PAGES.map((p) => {
        const mine = views.filter((v) => v.page === p.page)
        const here = p.to === '/' ? loc.pathname === '/' : loc.pathname.startsWith(p.to)
        const active = here && !mine.some((v) => String(v.id) === viewId)
        return (
          <NavLink key={p.to} label={p.label} leftSection={p.icon} component={Link} to={p.page ? `${p.to}?view=none` : p.to}
            active={active} aria-current={active ? 'page' : undefined} onClick={onNavigate} opened={mine.length > 0} childrenOffset={28}>
            {mine.length > 0 && mine.map((v) => {
              const on = here && String(v.id) === viewId
              return (
                <NavLink key={v.id} label={v.name} component={Link} to={stateUrl(v.page, v.state, meta, v.id)} onClick={onNavigate}
                  active={on} aria-current={on ? 'page' : undefined}
                  rightSection={v.count != null && <Badge size="xs" variant="light" aria-label={`${num(v.count)} matching`}>{num(v.count)}</Badge>} />
              )
            })}
          </NavLink>
        )
      })}
    </>
  )
}

/** "Skip to content": hidden until a keyboard user tabs onto it. */
function SkipLink() {
  const [shown, setShown] = useState(false)
  return (
    <Button component="a" href="#main" size="xs" onFocus={() => setShown(true)} onBlur={() => setShown(false)}
      onClick={(e) => { e.preventDefault(); document.getElementById('main')?.focus() }}
      style={shown
        ? { position: 'fixed', top: 8, left: 8, zIndex: 1000 }
        : { position: 'absolute', width: 1, height: 1, overflow: 'hidden', clipPath: 'inset(50%)', whiteSpace: 'nowrap' }}>
      Skip to Content
    </Button>
  )
}

export function App() {
  const [opened, { toggle, close }] = useDisclosure()
  const reduced = useReducedMotion()
  return (
    <AppShell header={{ height: 52 }} navbar={{ width: 240, breakpoint: 'sm', collapsed: { mobile: !opened } }} padding="md"
      transitionDuration={reduced ? 0 : undefined}>
      <SkipLink />
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group gap="xs">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label={opened ? 'Close navigation' : 'Open navigation'}
              aria-expanded={opened} aria-controls="app-nav" />
            <IconActivity size={20} aria-hidden />
            <Text fw={700} size="lg" component={Link} to="/" c="inherit" td="none" translate="no">housing-watch</Text>
          </Group>
          <Text size="xs" c="dimmed" visibleFrom="sm">NYC affordable housing</Text>
        </Group>
      </AppShell.Header>
      <AppShell.Navbar p="xs" id="app-nav" aria-label="Pages and saved views">
        <AppShell.Section grow component={ScrollArea}><Nav onNavigate={close} /></AppShell.Section>
      </AppShell.Navbar>
      <AppShell.Main id="main" tabIndex={-1}>
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/listings" element={<ListingsPage />} />
          <Route path="/units" element={<UnitsPage />} />
          <Route path="/events" element={<EventsPage />} />
          <Route path="/sources" element={<SourcesPage />} />
          <Route path="/fleet" element={<FleetPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AppShell.Main>
    </AppShell>
  )
}
