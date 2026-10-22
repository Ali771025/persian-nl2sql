import KeyRoundedIcon from '@mui/icons-material/KeyRounded'
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  InputAdornment,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useEffect, useState } from 'react'

import type { AuthConfig } from '../api/client'

export function SettingsDialog({
  open,
  value,
  onClose,
  onSave,
}: {
  open: boolean
  value: AuthConfig
  onClose: () => void
  onSave: (value: AuthConfig) => void
}) {
  const [draft, setDraft] = useState(value)
  useEffect(() => setDraft(value), [value, open])

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>تنظیم اتصال به سرویس</DialogTitle>
      <DialogContent>
        <Stack spacing={2.2} mt={1}>
          <Alert severity="info">
            کلیدها فقط در sessionStorage همین مرورگر نگه‌داری می‌شوند و با بستن نشست حذف خواهند شد.
          </Alert>
          <TextField
            label="آدرس پایه API"
            value={draft.apiBase}
            dir="ltr"
            onChange={(event) => setDraft({ ...draft, apiBase: event.target.value.replace(/\/$/, '') })}
            fullWidth
          />
          <TextField
            label="API Key"
            type="password"
            autoComplete="off"
            value={draft.apiKey}
            onChange={(event) => setDraft({ ...draft, apiKey: event.target.value })}
            fullWidth
            slotProps={{ input: { startAdornment: <InputAdornment position="start"><KeyRoundedIcon /></InputAdornment> } }}
          />
          <TextField
            label="Admin API Key"
            type="password"
            autoComplete="off"
            value={draft.adminKey}
            onChange={(event) => setDraft({ ...draft, adminKey: event.target.value })}
            fullWidth
          />
          <TextField
            label="Bearer JWT (در حالت SSO)"
            type="password"
            autoComplete="off"
            value={draft.bearerToken}
            onChange={(event) => setDraft({ ...draft, bearerToken: event.target.value })}
            fullWidth
            multiline
            minRows={2}
          />
          <Typography variant="caption" color="text.secondary">
            در حالت JWT، Bearer Token برای endpointهای کاربری جای API Key را می‌گیرد. عملیات admin همچنان به کلید مستقل نیاز دارد.
          </Typography>
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2.5 }}>
        <Button onClick={onClose} color="inherit">انصراف</Button>
        <Button onClick={() => onSave(draft)} variant="contained">ذخیره تنظیمات</Button>
      </DialogActions>
    </Dialog>
  )
}
