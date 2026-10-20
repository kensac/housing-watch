import { AppShell, Badge, Burger, Group, NavLink, ScrollArea, Text, Title } from '@mantine/core'
import { useDisclosure } from '@mantine/hooks'
import { IconActivity, IconBuilding, IconDoor, IconHome, IconList, IconServer, IconTimeline } from '@tabler/icons-react'
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom'

import type { Page } from './api'
import { stateUrl, useMeta, useViews } from './hooks'
import { EventsPage } from './pages/Events'
import { FleetPage } from './pages/Fleet'
import { ListingsPage } from './pages/Listings'
import { OverviewPage } from './pages/Overview'
import { SourcesPage } from './pages/Sources'
import { UnitsPage } from './pages/Units'

const PAGES: { to: string; label: string; icon: React.ReactNode; page?: Page }[] = [
  { to: '/', label: 'Overview', icon: <IconHome size={16} /> },
  { to: '/listings', label: 'Listings', icon: <IconBuilding size={16} />, page: 'listings' },
  { to: '/units', label: 'Units', icon: <IconDoor size={16} />, page: 'units' },
  { to: '/events', label: 'Events', icon: <IconTimeline size={16} />, page: 'events' },
  { to: '/sources', label: 'Sources', icon: <IconList size={16} /> },
  { to: '/fleet', label: 'Fleet', icon: <IconServer size={16} /> },
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
        return (
          <NavLink key={p.to} label={p.label} leftSection={p.icon} component={Link} to={p.page ? `${p.to}?view=none` : p.to}
            active={here && !mine.some((v) => String(v.id) === viewId)} onClick={onNavigate} opened={mine.length > 0} childrenOffset={28}>
            {mine.length > 0 && mine.map((v) => (
              <NavLink key={v.id} label={v.name} component={Link} to={stateUrl(v.page, v.state, meta, v.id)} onClick={onNavigate}
                active={here && String(v.id) === viewId} rightSection={v.count != null && <Badge size="xs" variant="light">{v.count}</Badge>} />
            ))}
          </NavLink>
        )
      })}
    </>
  )
}

export function App() {
  const [opened, { toggle, close }] = useDisclosure()
  return (
    <AppShell header={{ height: 52 }} navbar={{ width: 240, breakpoint: 'sm', collapsed: { mobile: !opened } }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group gap="xs">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" />
            <IconActivity size={20} />
            <Title order={4}>housing-watch</Title>
          </Group>
          <Text size="xs" c="dimmed" visibleFrom="sm">NYC affordable housing</Text>
        </Group>
      </AppShell.Header>
      <AppShell.Navbar p="xs">
        <AppShell.Section grow component={ScrollArea}><Nav onNavigate={close} /></AppShell.Section>
      </AppShell.Navbar>
      <AppShell.Main>
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
