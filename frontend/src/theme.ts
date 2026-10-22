import { createTheme } from '@mui/material/styles'

export const theme = createTheme({
  direction: 'rtl',
  palette: {
    mode: 'light',
    primary: { main: '#0E7490', dark: '#164E63', light: '#67E8F9' },
    secondary: { main: '#D97706', dark: '#92400E' },
    background: { default: '#F4F7FA', paper: '#FFFFFF' },
    text: { primary: '#102A43', secondary: '#627D98' },
    success: { main: '#16866B' },
    warning: { main: '#D97706' },
    error: { main: '#C2414B' },
  },
  typography: {
    fontFamily: 'Vazirmatn, Tahoma, Arial, sans-serif',
    h1: { fontWeight: 800 },
    h2: { fontWeight: 800 },
    h3: { fontWeight: 750 },
    h4: { fontWeight: 750 },
    h5: { fontWeight: 700 },
    h6: { fontWeight: 700 },
    button: { fontWeight: 700 },
  },
  shape: { borderRadius: 14 },
  components: {
    MuiCard: {
      styleOverrides: {
        root: {
          border: '1px solid #E6EDF3',
          boxShadow: '0 10px 30px rgba(16, 42, 67, 0.06)',
        },
      },
    },
    MuiButton: { defaultProps: { disableElevation: true } },
    MuiChip: { styleOverrides: { root: { fontWeight: 650 } } },
  },
})
