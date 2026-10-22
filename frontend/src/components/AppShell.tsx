import AccountTreeRoundedIcon from '@mui/icons-material/AccountTreeRounded'
import AdminPanelSettingsRoundedIcon from '@mui/icons-material/AdminPanelSettingsRounded'
import DashboardRoundedIcon from '@mui/icons-material/DashboardRounded'
import MenuRoundedIcon from '@mui/icons-material/MenuRounded'
import PsychologyAltRoundedIcon from '@mui/icons-material/PsychologyAltRounded'
import SettingsRoundedIcon from '@mui/icons-material/SettingsRounded'
import {
  AppBar,
  Box,
  Button,
  Chip,
  Divider,
  Drawer,
  IconButton,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Stack,
  Toolbar,
  Tooltip,
  Typography,
  useMediaQuery,
  useTheme,
} from '@mui/material'
import { useState, type ReactNode } from 'react'

export type PageId = 'overview' | 'query' | 'schema' | 'admin'

const drawerWidth = 274
const navigation = [
  { id: 'overview' as const, label: 'نمای کلی', icon: <DashboardRoundedIcon /> },
  { id: 'query' as const, label: 'پرسش از داده', icon: <PsychologyAltRoundedIcon /> },
  { id: 'schema' as const, label: 'ساختار و روابط', icon: <AccountTreeRoundedIcon /> },
  { id: 'admin' as const, label: 'مدیریت سامانه', icon: <AdminPanelSettingsRoundedIcon /> },
]

export function AppShell({
  page,
  onPage,
  onSettings,
  configured,
  children,
}: {
  page: PageId
  onPage: (page: PageId) => void
  onSettings: () => void
  configured: boolean
  children: ReactNode
}) {
  const theme = useTheme()
  const desktop = useMediaQuery(theme.breakpoints.up('md'))
  const [mobileOpen, setMobileOpen] = useState(false)

  const drawer = (
    <Box
      sx={{
        height: '100%',
        bgcolor: '#102A43',
        color: 'white',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <Box sx={{ p: 3, pb: 2.5 }}>
        <Stack direction="row" gap={1.5} alignItems="center">
          <Box
            sx={{
              width: 46,
              height: 46,
              borderRadius: 3,
              display: 'grid',
              placeItems: 'center',
              bgcolor: 'rgba(103,232,249,.16)',
              color: '#67E8F9',
            }}
          >
            <PsychologyAltRoundedIcon fontSize="large" />
          </Box>
          <Box>
            <Typography fontWeight={800} fontSize={17}>دستیار ماده ۱۰۰</Typography>
            <Typography variant="caption" sx={{ color: '#9FB3C8' }}>NL2SQL تخصصی شهرداری</Typography>
          </Box>
        </Stack>
      </Box>
      <Divider sx={{ borderColor: 'rgba(255,255,255,.09)' }} />
      <List sx={{ p: 1.5, flexGrow: 1 }}>
        {navigation.map((item) => (
          <ListItemButton
            key={item.id}
            selected={page === item.id}
            onClick={() => {
              onPage(item.id)
              setMobileOpen(false)
            }}
            sx={{
              borderRadius: 2.5,
              mb: 0.8,
              color: '#D9E2EC',
              '& .MuiListItemIcon-root': { color: 'inherit' },
              '&.Mui-selected': { bgcolor: 'rgba(103,232,249,.14)', color: '#67E8F9' },
              '&.Mui-selected:hover': { bgcolor: 'rgba(103,232,249,.19)' },
            }}
          >
            <ListItemIcon sx={{ minWidth: 42 }}>{item.icon}</ListItemIcon>
            <ListItemText primary={item.label} slotProps={{ primary: { fontWeight: 700 } }} />
          </ListItemButton>
        ))}
      </List>
      <Box sx={{ p: 2 }}>
        <Button
          fullWidth
          onClick={onSettings}
          startIcon={<SettingsRoundedIcon />}
          sx={{ color: 'white', justifyContent: 'flex-start', bgcolor: 'rgba(255,255,255,.06)' }}
        >
          تنظیم اتصال
        </Button>
        <Stack direction="row" alignItems="center" justifyContent="space-between" mt={2} px={0.5}>
          <Typography variant="caption" sx={{ color: '#829AB1' }}>نسخه ۰.۵.۰</Typography>
          <Chip
            size="small"
            label={configured ? 'متصل' : 'نیاز به کلید'}
            color={configured ? 'success' : 'warning'}
            variant="outlined"
          />
        </Stack>
      </Box>
    </Box>
  )

  return (
    <Box sx={{ display: 'flex', minHeight: '100vh' }}>
      {!desktop && (
        <AppBar position="fixed" color="inherit" elevation={0} sx={{ borderBottom: '1px solid #E6EDF3' }}>
          <Toolbar>
            <IconButton edge="start" onClick={() => setMobileOpen(true)}><MenuRoundedIcon /></IconButton>
            <Typography fontWeight={800} flexGrow={1}>دستیار ماده ۱۰۰</Typography>
            <Tooltip title="تنظیم اتصال"><IconButton onClick={onSettings}><SettingsRoundedIcon /></IconButton></Tooltip>
          </Toolbar>
        </AppBar>
      )}
      <Box component="nav" sx={{ width: { md: drawerWidth }, flexShrink: { md: 0 } }}>
        <Drawer
          variant={desktop ? 'permanent' : 'temporary'}
          open={desktop || mobileOpen}
          onClose={() => setMobileOpen(false)}
          ModalProps={{ keepMounted: true }}
          sx={{ '& .MuiDrawer-paper': { width: drawerWidth, border: 0 } }}
        >
          {drawer}
        </Drawer>
      </Box>
      <Box component="main" sx={{ flexGrow: 1, width: { md: `calc(100% - ${drawerWidth}px)` }, minWidth: 0 }}>
        <Box sx={{ height: { xs: 64, md: 0 } }} />
        <Box sx={{ p: { xs: 2, sm: 3, lg: 4 }, maxWidth: 1500, mx: 'auto' }}>{children}</Box>
      </Box>
    </Box>
  )
}
