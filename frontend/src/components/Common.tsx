import CheckCircleRoundedIcon from '@mui/icons-material/CheckCircleRounded'
import ErrorRoundedIcon from '@mui/icons-material/ErrorRounded'
import PendingRoundedIcon from '@mui/icons-material/PendingRounded'
import WarningAmberRoundedIcon from '@mui/icons-material/WarningAmberRounded'
import { Box, Card, CardContent, Chip, Skeleton, Stack, Typography } from '@mui/material'
import type { ReactNode } from 'react'

export function PageHeader({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string
  title: string
  description: string
  action?: ReactNode
}) {
  return (
    <Stack direction={{ xs: 'column', md: 'row' }} justifyContent="space-between" gap={2} mb={3}>
      <Box>
        <Typography variant="overline" color="primary" fontWeight={800} letterSpacing={1}>
          {eyebrow}
        </Typography>
        <Typography variant="h4" component="h1" sx={{ mt: 0.2 }}>
          {title}
        </Typography>
        <Typography color="text.secondary" sx={{ mt: 0.8, maxWidth: 760 }}>
          {description}
        </Typography>
      </Box>
      {action && <Box sx={{ alignSelf: { xs: 'stretch', md: 'center' } }}>{action}</Box>}
    </Stack>
  )
}

export function StatCard({
  title,
  value,
  caption,
  icon,
  loading = false,
}: {
  title: string
  value: string | number
  caption: string
  icon: ReactNode
  loading?: boolean
}) {
  return (
    <Card sx={{ height: '100%' }}>
      <CardContent>
        <Stack direction="row" alignItems="flex-start" justifyContent="space-between">
          <Box>
            <Typography variant="body2" color="text.secondary">
              {title}
            </Typography>
            {loading ? (
              <Skeleton width={80} height={46} />
            ) : (
              <Typography variant="h4" mt={0.8} fontWeight={800} dir="ltr">
                {value}
              </Typography>
            )}
          </Box>
          <Box
            sx={{
              width: 44,
              height: 44,
              borderRadius: 3,
              bgcolor: 'rgba(14,116,144,.1)',
              color: 'primary.main',
              display: 'grid',
              placeItems: 'center',
            }}
          >
            {icon}
          </Box>
        </Stack>
        <Typography variant="caption" color="text.secondary">
          {caption}
        </Typography>
      </CardContent>
    </Card>
  )
}

const statusMap: Record<string, { label: string; color: 'success' | 'warning' | 'error' | 'info' }> = {
  executed: { label: 'اجرا شد', color: 'success' },
  sql_generated: { label: 'SQL آماده', color: 'success' },
  needs_clarification: { label: 'نیاز به توضیح', color: 'warning' },
  access_denied: { label: 'عدم دسترسی', color: 'error' },
  validation_failed: { label: 'نامعتبر', color: 'error' },
  execution_failed: { label: 'خطای اجرا', color: 'error' },
  execution_disabled: { label: 'اجرا غیرفعال است', color: 'warning' },
  llm_not_configured: { label: 'LLM پیکربندی نشده', color: 'error' },
  llm_provider_not_available: { label: 'Provider در دسترس نیست', color: 'error' },
  llm_model_not_allowed: { label: 'مدل مجاز نیست', color: 'error' },
  index_not_ready: { label: 'دانش آماده نیست', color: 'error' },
  schema_version_not_found: { label: 'نسخه Schema یافت نشد', color: 'error' },
  knowledge_snapshot_inconsistent: { label: 'Snapshot ناسازگار', color: 'error' },
  generation_failed: { label: 'خطای تولید SQL', color: 'error' },
  llm_billing_inactive: { label: 'اعتبار LLM غیرفعال است', color: 'error' },
  llm_authentication_failed: { label: 'کلید LLM نامعتبر است', color: 'error' },
  llm_rate_limited: { label: 'محدودیت سرویس LLM', color: 'warning' },
  llm_timeout: { label: 'پاسخ‌ندادن Provider', color: 'warning' },
  running: { label: 'در حال اجرا', color: 'info' },
  queued: { label: 'در صف', color: 'warning' },
  succeeded: { label: 'موفق', color: 'success' },
  failed: { label: 'ناموفق', color: 'error' },
}

export function StatusChip({ status }: { status: string }) {
  const item = statusMap[status] ?? { label: status, color: 'info' as const }
  const icon =
    item.color === 'success' ? (
      <CheckCircleRoundedIcon />
    ) : item.color === 'error' ? (
      <ErrorRoundedIcon />
    ) : item.color === 'warning' ? (
      <WarningAmberRoundedIcon />
    ) : (
      <PendingRoundedIcon />
    )
  return <Chip size="small" label={item.label} color={item.color} icon={icon} />
}
