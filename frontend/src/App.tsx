import { AppShell, Burger, Button, Group, NavLink, ScrollArea, Stack } from '@mantine/core'
import { useDisclosure, useReducedMotion } from '@mantine/hooks'
import { IconActivity, IconBuilding, IconDoor, IconHome, IconList, IconServer, IconTimeline, IconChecklist } from '@tabler/icons-react'
import { useState } from 'react'
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom'

import type { Page } from './api'
import classes from './App.module.css'
import { num } from './format'
import { stateUrl, viewState } from './filters'
import { useMeta, useViews } from './hooks'
import { EventsPage } from './pages/Events'
import { FleetPage } from './pages/Fleet'
import { ListingsPage } from './pages/Listings'
import { OverviewPage } from './pages/Overview'
import { SourcesPage } from './pages/Sources'
import { TrackerPage } from './pages/Tracker'
import { UnitsPage } from './pages/Units'

const PAGES: { to: string; label: string; icon: React.ReactNode; page?: Page }[] = [
  { to: '/', label: 'Overview', icon: <IconHome size={16} aria-hidden /> },
  { to: '/listings', label: 'Listings', icon: <IconBuilding size={16} aria-hidden />, page: 'listings' },
  { to: '/units', label: 'Units', icon: <IconDoor size={16} aria-hidden />, page: 'units' },
  { to: '/events', label: 'Events', icon: <IconTimeline size={16} aria-hidden />, page: 'events' },
  { to: '/tracker', label: 'Tracker', icon: <IconChecklist size={16} aria-hidden /> },
  { to: '/sources', label: 'Sources', icon: <IconList size={16} aria-hidden /> },
  { to: '/fleet', label: 'Fleet', icon: <IconServer size={16} aria-hidden /> },
]

function Nav({ onNavigate }: { onNavigate: () => void }) {
  const loc = useLocation()
  const meta = useMeta().data
  const views = useViews().data ?? []
  const viewId = new URLSearchParams(loc.search).get('view')
  return (
    <Stack gap={2}>
      {PAGES.map((p) => {
        const mine = views.filter((v) => v.page === p.page)
        const here = p.to === '/' ? loc.pathname === '/' : loc.pathname.startsWith(p.to)
        const active = here && !mine.some((v) => v.id === viewId)
        return (
          <NavLink key={p.to} label={p.label} leftSection={p.icon} component={Link} to={p.page ? `${p.to}?view=none` : p.to}
            classNames={{ root: classes.link, section: classes.icon }}
            active={active} aria-current={active ? 'page' : undefined} onClick={onNavigate} opened={mine.length > 0} childrenOffset={26}>
            {mine.length > 0 && mine.map((v) => {
              const on = here && v.id === viewId
              return (
                <NavLink key={v.id} label={v.name} component={Link} to={stateUrl(viewState(v), meta, v.id)} onClick={onNavigate}
                  classNames={{ root: `${classes.link} ${classes.sub}` }} active={on} aria-current={on ? 'page' : undefined}
                  rightSection={v.count != null && <span className={classes.count} aria-label={`${num(v.count)} matching`}>{num(v.count)}</span>} />
              )
            })}
          </NavLink>
        )
      })}
    </Stack>
  )
}

const Brand = () => (
  <Link to="/" className={classes.brand} translate="no">
    <span className={classes.brandMark} aria-hidden><IconActivity size={14} /></span>
    housing-watch
  </Link>
)

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
    // the header exists only on phones, where it carries the burger; from sm up the brand sits in the sidebar
    <AppShell className={classes.shell} header={{ height: { base: 48, sm: 0 } }}
      navbar={{ width: 232, breakpoint: 'sm', collapsed: { mobile: !opened } }} padding={{ base: 'md', sm: 'lg' }}
      transitionDuration={reduced ? 0 : undefined}>
      <SkipLink />
      <AppShell.Header className={classes.header} hiddenFrom="sm">
        <Group h="100%" px="xs" gap={4}>
          <Burger opened={opened} onClick={toggle} size="sm" aria-label={opened ? 'Close navigation' : 'Open navigation'}
            aria-expanded={opened} aria-controls="app-nav" />
          <Brand />
        </Group>
      </AppShell.Header>
      <AppShell.Navbar className={classes.navbar} p="xs" id="app-nav" aria-label="Pages and saved views">
        <AppShell.Section visibleFrom="sm" mb="xs"><Brand /></AppShell.Section>
        <AppShell.Section grow component={ScrollArea}><Nav onNavigate={close} /></AppShell.Section>
      </AppShell.Navbar>
      <AppShell.Main id="main" tabIndex={-1}>
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/listings" element={<ListingsPage />} />
          <Route path="/units" element={<UnitsPage />} />
          <Route path="/events" element={<EventsPage />} />
          <Route path="/tracker" element={<TrackerPage />} />
          <Route path="/sources" element={<SourcesPage />} />
          <Route path="/fleet" element={<FleetPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AppShell.Main>
    </AppShell>
  )
}
