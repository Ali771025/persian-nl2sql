import { Box, CircularProgress } from '@mui/material'
import { lazy, Suspense, useMemo, useState } from 'react'

import { ApiClient, loadAuthConfig, normalizeAuthConfig, saveAuthConfig, type AuthConfig } from './api/client'
import { AppShell, type PageId } from './components/AppShell'
import { SettingsDialog } from './components/SettingsDialog'

const AdminPage = lazy(() => import('./pages/AdminPage').then((module) => ({ default: module.AdminPage })))
const OverviewPage = lazy(() => import('./pages/OverviewPage').then((module) => ({ default: module.OverviewPage })))
const QueryPage = lazy(() => import('./pages/QueryPage').then((module) => ({ default: module.QueryPage })))
const SchemaPage = lazy(() => import('./pages/SchemaPage').then((module) => ({ default: module.SchemaPage })))

export default function App() {
  const [page, setPage] = useState<PageId>('query')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [auth, setAuth] = useState<AuthConfig>(() => loadAuthConfig())
  const api = useMemo(() => new ApiClient(auth), [auth])
  const configured = Boolean(auth.apiKey || auth.bearerToken)

  const save = (value: AuthConfig) => {
    const normalized = normalizeAuthConfig(value)
    setAuth(normalized)
    saveAuthConfig(normalized)
    setSettingsOpen(false)
  }

  return (
    <AppShell
      page={page}
      onPage={setPage}
      onSettings={() => setSettingsOpen(true)}
      configured={configured}
    >
      <Suspense fallback={<Box sx={{ display: 'grid', placeItems: 'center', minHeight: 400 }}><CircularProgress /></Box>}>
        {page === 'overview' && <OverviewPage api={api} configured={configured} />}
        {page === 'query' && <QueryPage api={api} configured={configured} />}
        {page === 'schema' && <SchemaPage api={api} configured={configured} />}
        {page === 'admin' && <AdminPage api={api} adminConfigured={Boolean(auth.adminKey)} />}
      </Suspense>
      <SettingsDialog open={settingsOpen} value={auth} onClose={() => setSettingsOpen(false)} onSave={save} />
    </AppShell>
  )
}
