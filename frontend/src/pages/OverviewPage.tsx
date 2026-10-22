import AccountTreeRoundedIcon from '@mui/icons-material/AccountTreeRounded'
import DatasetRoundedIcon from '@mui/icons-material/DatasetRounded'
import HubRoundedIcon from '@mui/icons-material/HubRounded'
import VerifiedRoundedIcon from '@mui/icons-material/VerifiedRounded'
import {
  Alert,
  Box,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  Stack,
  Typography,
} from '@mui/material'
import { useEffect, useState } from 'react'

import type { ApiClient } from '../api/client'
import type { DomainOverview, Health, ServiceMeta } from '../api/types'
import { PageHeader, StatCard } from '../components/Common'

const featureLabels: Record<string, string> = {
  commission100_semantic_layer: 'لایه معنایی ماده ۱۰۰',
  hybrid_schema_rag: 'Hybrid Schema-RAG',
  transformer_reranker: 'Transformer Reranker',
  deterministic_recipes: 'Recipeهای قطعی',
  tsql_validation: 'اعتبارسنجی T-SQL',
  rbac_abac: 'RBAC / ABAC',
  incremental_ingestion: 'Ingestion افزایشی',
  metadata_audit: 'Audit متادیتایی',
  feedback: 'چرخه بازخورد',
}

export function OverviewPage({ api, configured }: { api: ApiClient; configured: boolean }) {
  const [health, setHealth] = useState<Health | null>(null)
  const [meta, setMeta] = useState<ServiceMeta | null>(null)
  const [domain, setDomain] = useState<DomainOverview | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    Promise.all([api.health(), api.meta()])
      .then(([healthResult, metaResult]) => {
        if (!active) return
        setHealth(healthResult)
        setMeta(metaResult)
      })
      .catch((reason: Error) => active && setError(reason.message))
    if (configured) {
      api.domain().then((result) => active && setDomain(result)).catch(() => undefined)
    }
    return () => {
      active = false
    }
  }, [api, configured])

  return (
    <>
      <PageHeader
        eyebrow="مرکز کنترل"
        title="نمای کلی سامانه"
        description="وضعیت مدل‌ها، دانش دامنه و آمادگی سرویس برای تولید T-SQL امن را یک‌جا ببینید."
        action={
          <Chip
            label={health?.status === 'ok' ? 'سرویس آماده است' : 'سرویس نیاز به بررسی دارد'}
            color={health?.status === 'ok' ? 'success' : 'warning'}
            icon={health ? <VerifiedRoundedIcon /> : <CircularProgress size={16} />}
          />
        }
      />
      {error && <Alert severity="error" sx={{ mb: 3 }}>{error}</Alert>}
      {!configured && (
        <Alert severity="warning" sx={{ mb: 3 }}>
          برای مشاهده دانش دامنه و ارسال پرسش، از بخش «تنظیم اتصال» API Key یا JWT را وارد کنید.
        </Alert>
      )}
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, 1fr)', xl: 'repeat(4, 1fr)' },
          gap: 2,
          mb: 3,
        }}
      >
        <StatCard title="Artifactهای دانش" value={health?.artifact_count ?? '—'} caption="واحدهای قابل بازیابی" icon={<DatasetRoundedIcon />} loading={!health} />
        <StatCard title="جدول‌های Schema" value={health?.table_count ?? '—'} caption="با کنترل دسترسی نقش" icon={<AccountTreeRoundedIcon />} loading={!health} />
        <StatCard title="Ontology" value={health?.ontology_version ?? '—'} caption="نسخه قواعد ماده ۱۰۰" icon={<HubRoundedIcon />} loading={!health} />
        <StatCard title="نسخه محصول" value={meta?.version ?? '—'} caption={`احراز هویت: ${meta?.auth_mode ?? '—'}`} icon={<VerifiedRoundedIcon />} loading={!meta} />
      </Box>

      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: '1.25fr .75fr' }, gap: 2 }}>
        <Card>
          <CardContent sx={{ p: 3 }}>
            <Typography variant="h6">قابلیت‌های فعال</Typography>
            <Typography variant="body2" color="text.secondary" mb={2.5}>
              زنجیره تولید SQL از تشخیص مفهوم تا کنترل دسترسی و audit.
            </Typography>
            <Stack direction="row" gap={1} flexWrap="wrap" useFlexGap>
              {(meta?.features ?? []).map((feature) => (
                <Chip key={feature} label={featureLabels[feature] ?? feature} variant="outlined" color="primary" />
              ))}
            </Stack>
            <Divider sx={{ my: 3 }} />
            <Stack direction={{ xs: 'column', sm: 'row' }} gap={2}>
              <Box flex={1}>
                <Typography variant="body2" color="text.secondary">Embedding</Typography>
                <Typography fontWeight={700} dir="ltr" textAlign="right">{health?.embedding_model ?? '—'}</Typography>
              </Box>
              <Box flex={1}>
                <Typography variant="body2" color="text.secondary">Reranker</Typography>
                <Typography fontWeight={700} dir="ltr" textAlign="right">{health?.reranker_model ?? '—'}</Typography>
              </Box>
              <Box flex={1}>
                <Typography variant="body2" color="text.secondary">
                  مدل مولد{health?.llm_provider ? ` (${health.llm_provider})` : ''}
                </Typography>
                <Typography fontWeight={700} dir="ltr" textAlign="right">{health?.llm_model ?? 'فقط recipe'}</Typography>
              </Box>
            </Stack>
          </CardContent>
        </Card>
        <Card>
          <CardContent sx={{ p: 3 }}>
            <Typography variant="h6">دانش تخصصی</Typography>
            <Typography variant="body2" color="text.secondary" mb={2.5}>
              موارد قابل استفاده برای نقش فعلی شما.
            </Typography>
            <Stack spacing={2}>
              <Stack direction="row" justifyContent="space-between"><Typography>مفاهیم</Typography><Chip label={domain?.concepts.length ?? '—'} /></Stack>
              <Stack direction="row" justifyContent="space-between"><Typography>شاخص‌ها</Typography><Chip label={domain?.metrics.length ?? '—'} /></Stack>
              <Stack direction="row" justifyContent="space-between"><Typography>قواعد کسب‌وکار</Typography><Chip label={domain?.business_rules.length ?? '—'} /></Stack>
              <Stack direction="row" justifyContent="space-between"><Typography>Recipeهای قطعی</Typography><Chip color="secondary" label={domain?.recipes.length ?? '—'} /></Stack>
            </Stack>
          </CardContent>
        </Card>
      </Box>
    </>
  )
}
