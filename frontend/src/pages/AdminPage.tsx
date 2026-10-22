import AssessmentRoundedIcon from '@mui/icons-material/AssessmentRounded'
import AutorenewRoundedIcon from '@mui/icons-material/AutorenewRounded'
import CloudSyncRoundedIcon from '@mui/icons-material/CloudSyncRounded'
import DataUsageRoundedIcon from '@mui/icons-material/DataUsageRounded'
import RefreshRoundedIcon from '@mui/icons-material/RefreshRounded'
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  LinearProgress,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material'
import { useCallback, useEffect, useState } from 'react'

import type { ApiClient } from '../api/client'
import type {
  AuditSummary,
  BackgroundJob,
  DatabaseStatus,
  EvaluationReport,
  FeedbackSummary,
  IndexStatus,
  KnowledgeConsistency,
  ProductionCheck,
} from '../api/types'
import { PageHeader, StatCard, StatusChip } from '../components/Common'

export function AdminPage({ api, adminConfigured }: { api: ApiClient; adminConfigured: boolean }) {
  const [index, setIndex] = useState<IndexStatus | null>(null)
  const [consistency, setConsistency] = useState<KnowledgeConsistency | null>(null)
  const [database, setDatabase] = useState<DatabaseStatus | null>(null)
  const [jobs, setJobs] = useState<BackgroundJob[]>([])
  const [audit, setAudit] = useState<AuditSummary | null>(null)
  const [feedback, setFeedback] = useState<FeedbackSummary | null>(null)
  const [evaluation, setEvaluation] = useState<EvaluationReport | null>(null)
  const [productionCheck, setProductionCheck] = useState<ProductionCheck | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    if (!adminConfigured) return
    const results = await Promise.allSettled([
      api.indexStatus(),
      api.jobs(),
      api.auditSummary(),
      api.feedbackSummary(),
      api.databaseStatus(),
      api.knowledgeConsistency(),
    ])
    if (results[0].status === 'fulfilled') setIndex(results[0].value)
    if (results[1].status === 'fulfilled') setJobs(results[1].value)
    if (results[2].status === 'fulfilled') setAudit(results[2].value)
    if (results[3].status === 'fulfilled') setFeedback(results[3].value)
    if (results[4].status === 'fulfilled') setDatabase(results[4].value)
    if (results[5].status === 'fulfilled') setConsistency(results[5].value)
    const rejected = results.find((item) => item.status === 'rejected')
    if (rejected?.status === 'rejected') setError(rejected.reason instanceof Error ? rejected.reason.message : 'خطای دریافت اطلاعات')
  }, [api, adminConfigured])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    if (!jobs.some((job) => job.state === 'queued' || job.state === 'running')) return
    const timer = window.setInterval(() => void refresh(), 2500)
    return () => window.clearInterval(timer)
  }, [jobs, refresh])

  const startIngest = async () => {
    setBusy(true)
    setError('')
    try {
      const job = await api.startIngest()
      setJobs((current) => [job, ...current])
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'شروع ingestion ناموفق بود')
    } finally {
      setBusy(false)
    }
  }

  const runEvaluation = async () => {
    setBusy(true)
    setError('')
    try {
      setEvaluation(await api.evaluate())
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'ارزیابی ناموفق بود')
    } finally {
      setBusy(false)
    }
  }

  const runProductionCheck = async () => {
    setBusy(true)
    setError('')
    try {
      setProductionCheck(await api.productionCheck())
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'بررسی نهایی production ناموفق بود')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Operations"
        title="مدیریت سامانه"
        description="وضعیت index، ingestion افزایشی، ارزیابی دامنه و شاخص‌های audit را مدیریت کنید."
        action={<Button startIcon={<RefreshRoundedIcon />} onClick={() => void refresh()} disabled={!adminConfigured}>به‌روزرسانی</Button>}
      />
      {!adminConfigured && <Alert severity="warning" sx={{ mb: 3 }}>برای استفاده از این صفحه Admin API Key را در تنظیم اتصال وارد کنید.</Alert>}
      {error && <Alert severity="error" onClose={() => setError('')} sx={{ mb: 3 }}>{error}</Alert>}
      {consistency && !consistency.consistent && (
        <Alert severity="error" sx={{ mb: 3 }}>
          Snapshot مخازن همگام نیست: {consistency.issues.join('، ')}. تا رفع اختلاف، readiness سرویس بسته می‌ماند.
        </Alert>
      )}
      {busy && <LinearProgress sx={{ mb: 2 }} />}
      {productionCheck && (
        <Alert severity={productionCheck.ready_for_production ? 'success' : 'warning'} sx={{ mb: 3 }}>
          بررسی Production نسخه {productionCheck.version}: {productionCheck.ready_for_production ? 'همه کنترل‌ها موفق بود' : 'برخی کنترل‌ها نیازمند اصلاح هستند'} — سؤال حیاتی: {productionCheck.critical_status}
        </Alert>
      )}

      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, 1fr)', xl: 'repeat(5, 1fr)' }, gap: 2, mb: 3 }}>
        <StatCard title="نسل Index" value={index?.generation ?? '—'} caption={`${index?.artifact_count ?? 0} artifact`} icon={<DataUsageRoundedIcon />} loading={adminConfigured && !index} />
        <StatCard title="نسبت Reuse" value={index ? `${Math.round(index.vector_reuse_ratio * 100)}٪` : '—'} caption="بردارهای بدون تغییر" icon={<AutorenewRoundedIcon />} loading={adminConfigured && !index} />
        <StatCard title="درخواست‌های Audit" value={audit?.total_queries ?? '—'} caption={`میانگین ${audit?.average_duration_ms ?? 0} ms`} icon={<AssessmentRoundedIcon />} loading={adminConfigured && !audit} />
        <StatCard title="بازخورد ثبت‌شده" value={feedback?.total ?? '—'} caption="بدون متن آزاد" icon={<CloudSyncRoundedIcon />} loading={adminConfigured && !feedback} />
        <StatCard title="Migration دیتابیس" value={database?.migration_revision ?? '—'} caption={`SQLAlchemy / ${database?.dialect ?? '—'}`} icon={<DataUsageRoundedIcon />} loading={adminConfigured && !database} />
      </Box>

      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', xl: '1fr 1fr' }, gap: 2, mb: 3 }}>
        <Card>
          <CardContent sx={{ p: 3 }}>
            <Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" gap={2}>
              <Box><Typography variant="h6">Ingestion افزایشی</Typography><Typography variant="body2" color="text.secondary">اسناد تغییرکرده embed و بقیه بردارها reuse می‌شوند.</Typography></Box>
              <Button variant="contained" startIcon={<CloudSyncRoundedIcon />} onClick={() => void startIngest()} disabled={!adminConfigured || busy || jobs.some((job) => ['queued', 'running'].includes(job.state))}>شروع Job</Button>
            </Stack>
            <Stack direction="row" gap={1} flexWrap="wrap" useFlexGap mt={3}>
              <Chip label={`مدل: ${index?.embedding_model ?? '—'}`} dir="ltr" />
              <Chip label={`ابعاد: ${index?.embedding_dimensions ?? '—'}`} />
              <Chip label={`FK: ${index?.foreign_key_count ?? '—'}`} />
              <Chip color={index?.mode === 'distributed' ? 'secondary' : 'default'} label={`RAG: ${index?.mode ?? '—'}`} />
              {index && Object.entries(index.stores).map(([name, store]) => (
                <Chip key={name} color={store.connected ? 'success' : store.enabled ? 'error' : 'default'} label={`${name}: ${store.connected ? 'online' : store.enabled ? 'offline' : 'disabled'}`} />
              ))}
              <Chip color={index?.ready ? 'success' : 'warning'} label={index?.ready ? 'آماده' : 'آماده نیست'} />
              <Chip
                color={consistency?.consistent ? 'success' : 'error'}
                label={consistency?.consistent ? 'Snapshot همگام' : 'Snapshot ناسازگار'}
              />
              {consistency?.expected_snapshot_id && (
                <Chip label={`Snapshot: ${consistency.expected_snapshot_id.slice(0, 12)}`} dir="ltr" />
              )}
            </Stack>
          </CardContent>
        </Card>
        <Card>
          <CardContent sx={{ p: 3 }}>
            <Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" gap={2}>
              <Box><Typography variant="h6">Gold Evaluation</Typography><Typography variant="body2" color="text.secondary">Regression قواعد قطعی ماده ۱۰۰ پیش از انتشار.</Typography></Box>
              <Stack direction="row" gap={1}>
                <Button variant="outlined" startIcon={busy ? <CircularProgress size={18} /> : <AssessmentRoundedIcon />} onClick={() => void runEvaluation()} disabled={!adminConfigured || busy}>Gold Suite</Button>
                <Button variant="contained" startIcon={<CloudSyncRoundedIcon />} onClick={() => void runProductionCheck()} disabled={!adminConfigured || busy}>Production Check</Button>
              </Stack>
            </Stack>
            {evaluation ? (
              <Stack mt={3} spacing={1}>
                <Stack direction="row" justifyContent="space-between"><Typography fontWeight={700}>{evaluation.suite}</Typography><Chip color={evaluation.pass_rate === 1 ? 'success' : 'error'} label={`${evaluation.passed}/${evaluation.total}`} /></Stack>
                <LinearProgress variant="determinate" value={evaluation.pass_rate * 100} color={evaluation.pass_rate === 1 ? 'success' : 'error'} />
                <Paper variant="outlined" sx={{ mt: 1, maxHeight: 560, overflow: 'auto' }}>
                  <Table size="small" stickyHeader>
                    <TableHead><TableRow><TableCell>نتیجه</TableCell><TableCell>سؤال کارفرما</TableCell><TableCell>Recipe</TableCell><TableCell>SQL اعتبارسنجی‌شده</TableCell></TableRow></TableHead>
                    <TableBody>
                      {evaluation.cases.map((item) => (
                        <TableRow key={item.case_id} sx={{ verticalAlign: 'top' }}>
                          <TableCell><Chip size="small" color={item.passed ? 'success' : 'error'} label={item.passed ? 'قبول' : 'رد'} /></TableCell>
                          <TableCell sx={{ minWidth: 240 }}><Typography variant="body2">{item.question}</Typography>{item.failures.length > 0 && <Typography variant="caption" color="error">{item.failures.join('؛ ')}</Typography>}</TableCell>
                          <TableCell dir="ltr"><Typography variant="caption">{item.recipe_id ?? '—'}</Typography></TableCell>
                          <TableCell dir="ltr" sx={{ minWidth: 480 }}><Box component="pre" sx={{ m: 0, whiteSpace: 'pre-wrap', fontSize: 11, fontFamily: 'monospace' }}>{item.sql ?? '—'}</Box></TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </Paper>
              </Stack>
            ) : <Alert severity="info" icon={false} sx={{ mt: 3 }}>هنوز در این نشست ارزیابی اجرا نشده است.</Alert>}
          </CardContent>
        </Card>
      </Box>

      <Card>
        <CardContent sx={{ p: 3 }}>
          <Typography variant="h6" mb={2}>Jobهای اخیر</Typography>
          <Paper variant="outlined" sx={{ overflow: 'auto' }}>
            <Table size="small">
              <TableHead><TableRow><TableCell>شناسه</TableCell><TableCell>مرحله</TableCell><TableCell>پیشرفت</TableCell><TableCell>وضعیت</TableCell><TableCell>زمان ایجاد</TableCell><TableCell>نتیجه</TableCell></TableRow></TableHead>
              <TableBody>
                {jobs.map((job) => <TableRow key={job.job_id}><TableCell dir="ltr">{job.job_id.slice(0, 12)}</TableCell><TableCell dir="ltr">{job.phase}</TableCell><TableCell sx={{ minWidth: 120 }}><Stack direction="row" alignItems="center" gap={1}><LinearProgress variant="determinate" value={job.progress_percent} sx={{ width: 72 }} /><Typography variant="caption">{job.progress_percent}٪</Typography></Stack></TableCell><TableCell><StatusChip status={job.state} /></TableCell><TableCell>{new Date(job.created_at).toLocaleString('fa-IR')}</TableCell><TableCell>{job.error_code ?? (job.result ? `snapshot ${job.snapshot_id.slice(0, 12)}` : '—')}</TableCell></TableRow>)}
                {jobs.length === 0 && <TableRow><TableCell colSpan={6}><Typography color="text.secondary" textAlign="center" py={2}>Job ثبت‌شده‌ای وجود ندارد.</Typography></TableCell></TableRow>}
              </TableBody>
            </Table>
          </Paper>
        </CardContent>
      </Card>
    </>
  )
}
