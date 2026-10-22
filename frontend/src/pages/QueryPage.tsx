import ContentCopyRoundedIcon from '@mui/icons-material/ContentCopyRounded'
import PlayArrowRoundedIcon from '@mui/icons-material/PlayArrowRounded'
import RestartAltRoundedIcon from '@mui/icons-material/RestartAltRounded'
import SendRoundedIcon from '@mui/icons-material/SendRounded'
import ThumbDownAltOutlinedIcon from '@mui/icons-material/ThumbDownAltOutlined'
import ThumbUpAltOutlinedIcon from '@mui/icons-material/ThumbUpAltOutlined'
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  IconButton,
  LinearProgress,
  InputLabel,
  MenuItem,
  Paper,
  Stack,
  Select,
  Switch,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tabs,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import { useEffect, useState } from 'react'

import type { ApiClient } from '../api/client'
import type { LLMProviderOption, QueryResponse } from '../api/types'
import { PageHeader, StatusChip } from '../components/Common'

const examples = [
  'شماره آخرین رأی ماده ۱۰۰ پرونده شماره ۳۰۰۰۱',
  'جمع جریمه‌های آخرین رأی پرونده ماده ۱۰۰ به شماره ۵۰۰۰',
  'آخرین وضعیت خلاف پرونده ماده ۱۰۰ به شماره ۳۰۰۰۱ چیست؟',
  'تمام کاربران را همراه تعداد پرونده‌های ثبت‌شده نمایش بده',
]

function valueText(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export function QueryPage({ api, configured }: { api: ApiClient; configured: boolean }) {
  const [question, setQuestion] = useState('')
  const [execute, setExecute] = useState(false)
  const [includeContext, setIncludeContext] = useState(false)
  const [schemaVersion, setSchemaVersion] = useState('')
  const [schemaVersions, setSchemaVersions] = useState<string[]>([])
  const [executionAvailable, setExecutionAvailable] = useState(false)
  const [llmOptions, setLlmOptions] = useState<LLMProviderOption[]>([])
  const [llmProvider, setLlmProvider] = useState('')
  const [llmModel, setLlmModel] = useState('')
  const [loading, setLoading] = useState(false)
  const [response, setResponse] = useState<QueryResponse | null>(null)
  const [error, setError] = useState('')
  const [tab, setTab] = useState(0)
  const [feedbackSent, setFeedbackSent] = useState(false)
  const [feedbackDialog, setFeedbackDialog] = useState(false)
  const [feedbackReason, setFeedbackReason] = useState('wrong_filter')
  const [turns, setTurns] = useState<{ question: string; response: QueryResponse }[]>([])

  useEffect(() => {
    if (!configured) return
    void api.schemaVersions().then((payload) => setSchemaVersions(payload.versions)).catch(() => undefined)
    void api.meta().then((payload) => {
      setExecutionAvailable(payload.execution_available)
      if (!payload.execution_available) setExecute(false)
      setLlmOptions(payload.llm_options)
      const provider = payload.default_llm_provider || payload.llm_options[0]?.provider || ''
      const option = payload.llm_options.find((item) => item.provider === provider)
      setLlmProvider(provider)
      setLlmModel(payload.default_llm_model || option?.models[0] || '')
    }).catch(() => setExecutionAvailable(false))
  }, [api, configured])

  const submit = async () => {
    if (question.trim().length < 3 || !configured) return
    setLoading(true)
    setError('')
    setFeedbackSent(false)
    try {
      const result = await api.query(
        question.trim(),
        execute,
        includeContext,
        schemaVersion.trim(),
        llmProvider,
        llmModel,
      )
      setResponse(result)
      setTurns((current) => [...current, { question: question.trim(), response: result }])
      setTab(result.result || result.error || result.clarification_question ? 0 : 1)
      setQuestion('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'خطای ناشناخته')
    } finally {
      setLoading(false)
    }
  }

  const sendFeedback = async (rating: 'helpful' | 'not_helpful', reason: string) => {
    if (!response) return
    try {
      await api.feedback(response.request_id, rating, reason)
      setFeedbackSent(true)
      setFeedbackDialog(false)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'ثبت بازخورد انجام نشد')
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="فضای پرس‌وجو"
        title="گفتگو با Schema Copilot ماده ۱۰۰"
        description="سؤال را به فارسی بنویسید؛ سامانه مفهوم، schema و روابط را بازیابی و یک T-SQL امن تولید می‌کند."
        action={turns.length > 0 ? <Button startIcon={<RestartAltRoundedIcon />} onClick={() => { setTurns([]); setResponse(null) }}>گفتگوی جدید</Button> : undefined}
      />
      {!configured && <Alert severity="warning" sx={{ mb: 3 }}>ابتدا کلید دسترسی یا JWT را در تنظیم اتصال وارد کنید.</Alert>}
      {error && <Alert severity="error" onClose={() => setError('')} sx={{ mb: 3 }}>{error}</Alert>}

      {turns.length > 0 && (
        <Stack spacing={1.5} mb={3}>
          {turns.map((turn, index) => (
            <Box key={`${turn.response.request_id}-${index}`}>
              <Paper sx={{ p: 1.5, mr: { xs: 0, md: 8 }, bgcolor: 'primary.main', color: 'primary.contrastText', borderRadius: 3 }}>
                <Typography>{turn.question}</Typography>
              </Paper>
              <Paper variant="outlined" sx={{ p: 1.5, mt: 1, ml: { xs: 0, md: 8 }, borderRadius: 3 }}>
                <Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" gap={1}>
                  <Box><StatusChip status={turn.response.status} /><Typography mt={1}>{turn.response.answer_fa ?? turn.response.explanation_fa ?? turn.response.error}</Typography></Box>
                  <Button size="small" onClick={() => setResponse(turn.response)}>جزئیات SQL</Button>
                </Stack>
              </Paper>
            </Box>
          ))}
        </Stack>
      )}

      <Card sx={{ mb: 3, overflow: 'visible' }}>
        <CardContent sx={{ p: { xs: 2, md: 3 } }}>
          <TextField
            fullWidth
            multiline
            minRows={4}
            maxRows={9}
            label="سؤال شما"
            placeholder="مثلاً: جمع جریمه‌های آخرین رأی پرونده ماده ۱۰۰ به شماره ۵۰۰۰"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') void submit()
            }}
            disabled={loading}
          />
          <Stack direction="row" gap={1} flexWrap="wrap" useFlexGap mt={1.5}>
            {examples.map((example) => (
              <Chip key={example} label={example} variant="outlined" size="small" onClick={() => setQuestion(example)} />
            ))}
          </Stack>
          <Divider sx={{ my: 2.5 }} />
          <Stack direction={{ xs: 'column', md: 'row' }} gap={1.5} mb={2}>
            <TextField
              select
              size="small"
              fullWidth
              label="Provider مدل"
              value={llmProvider}
              onChange={(event) => {
                const provider = event.target.value
                const option = llmOptions.find((item) => item.provider === provider)
                setLlmProvider(provider)
                setLlmModel(option?.models[0] || '')
              }}
              disabled={loading || llmOptions.length === 0}
              helperText={llmOptions.length === 0 ? 'فقط recipeهای قطعی در دسترس‌اند' : 'کلید Provider فقط در بک‌اند نگه‌داری می‌شود'}
            >
              {llmOptions.map((option) => (
                <MenuItem key={option.provider} value={option.provider}>{option.label}</MenuItem>
              ))}
            </TextField>
            <TextField
              select
              size="small"
              fullWidth
              label="مدل"
              value={llmModel}
              onChange={(event) => setLlmModel(event.target.value)}
              disabled={loading || !llmProvider}
            >
              {(llmOptions.find((item) => item.provider === llmProvider)?.models ?? []).map((model) => (
                <MenuItem key={model} value={model} dir="ltr">{model}</MenuItem>
              ))}
            </TextField>
            <TextField
              select
              size="small"
              fullWidth
              label="نسخه Schema (اختیاری)"
              placeholder="خالی یعنی نسخه فعال"
              value={schemaVersion}
              onChange={(event) => setSchemaVersion(event.target.value)}
              disabled={loading}
            >
              <MenuItem value="">نسخه فعال</MenuItem>
              {schemaVersions.map((version) => <MenuItem key={version} value={version}>{version}</MenuItem>)}
            </TextField>
          </Stack>
          <Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" alignItems={{ sm: 'center' }} gap={2}>
            <Stack direction="row" gap={2} flexWrap="wrap">
              <Tooltip title={executionAvailable ? '' : 'اتصال Read-only دیتابیس در این محیط آماده نیست'}>
                <span>
                  <FormControlLabel
                    disabled={!executionAvailable}
                    control={<Switch checked={execute} onChange={(_, value) => setExecute(value)} />}
                    label="اجرای Read-only"
                  />
                </span>
              </Tooltip>
              <FormControlLabel control={<Switch checked={includeContext} onChange={(_, value) => setIncludeContext(value)} />} label="نمایش context" />
            </Stack>
            <Button
              variant="contained"
              size="large"
              endIcon={loading ? <CircularProgress size={19} color="inherit" /> : execute ? <PlayArrowRoundedIcon /> : <SendRoundedIcon />}
              disabled={loading || !configured || question.trim().length < 3}
              onClick={() => void submit()}
              sx={{ minWidth: 170 }}
            >
              {loading ? 'در حال پردازش…' : execute ? 'تولید و اجرا' : 'تولید SQL'}
            </Button>
          </Stack>
        </CardContent>
        {loading && <LinearProgress />}
      </Card>

      {response && (
        <Card>
          <CardContent sx={{ p: 0 }}>
            <Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" alignItems={{ sm: 'center' }} gap={2} p={2.5}>
              <Stack direction="row" alignItems="center" gap={1.5} flexWrap="wrap">
                <StatusChip status={response.status} />
                <Typography fontWeight={700}>
                  {response.answer_fa ?? response.explanation_fa ?? response.clarification_question ?? response.error ?? 'پاسخی تولید نشد.'}
                </Typography>
                {!response.error && <Chip size="small" label={`اطمینان ${Math.round(response.confidence * 100)}٪`} variant="outlined" />}
              </Stack>
              <Stack direction="row" gap={0.5} alignItems="center">
                <Typography variant="caption" color="text.secondary">این پاسخ مفید بود؟</Typography>
                <Tooltip title="مفید"><span><IconButton size="small" color="success" disabled={feedbackSent} onClick={() => void sendFeedback('helpful', 'correct')}><ThumbUpAltOutlinedIcon /></IconButton></span></Tooltip>
                <Tooltip title="نیاز به اصلاح"><span><IconButton size="small" color="error" disabled={feedbackSent} onClick={() => setFeedbackDialog(true)}><ThumbDownAltOutlinedIcon /></IconButton></span></Tooltip>
                {feedbackSent && <Chip size="small" color="success" label="ثبت شد" />}
              </Stack>
            </Stack>
            {(response.error || response.clarification_question) && (
              <Alert severity={response.clarification_question ? 'warning' : 'error'} sx={{ mx: 2.5, mb: 2 }}>
                {response.clarification_question ?? response.error}
              </Alert>
            )}
            <Divider />
            <Tabs value={tab} onChange={(_, value: number) => setTab(value)} variant="scrollable" allowScrollButtonsMobile>
              <Tab label={`نتیجه${response.result ? ` (${response.result.row_count})` : ''}`} />
              <Tab label="SQL" />
              <Tab label="تحلیل دامنه" />
              <Tab label="مسیر پردازش" />
            </Tabs>
            <Divider />
            <Box p={{ xs: 2, md: 3 }}>
              {tab === 0 && (
                response.result ? (
                  <TableContainer component={Paper} variant="outlined" sx={{ maxHeight: 520 }}>
                    <Table stickyHeader size="small">
                      <TableHead><TableRow>{response.result.columns.map((column) => <TableCell key={column} sx={{ fontWeight: 800 }} dir="ltr">{column}</TableCell>)}</TableRow></TableHead>
                      <TableBody>{response.result.rows.map((row, index) => <TableRow key={index} hover>{response.result!.columns.map((column) => <TableCell key={column}>{valueText(row[column])}</TableCell>)}</TableRow>)}</TableBody>
                    </Table>
                  </TableContainer>
                ) : response.error ? (
                  <Alert severity="error">{response.error}{response.failure_stage ? ` (مرحله: ${response.failure_stage})` : ''}</Alert>
                ) : response.clarification_question ? (
                  <Alert severity="warning">{response.clarification_question}</Alert>
                ) : <Alert severity="info">SQL تولید شده است؛ اجرای دیتابیس برای این درخواست انجام نشده.</Alert>
              )}
              {tab === 1 && (
                <Stack spacing={2}>
                  <Box position="relative">
                    <Paper variant="outlined" sx={{ p: 2.5, bgcolor: '#0B1F33', color: '#D9EAF7', overflow: 'auto' }}>
                      <Typography component="pre" dir="ltr" sx={{ m: 0, fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace', fontSize: 14, whiteSpace: 'pre-wrap', textAlign: 'left' }}>
                        {response.sql ?? 'SQL تولید نشده است.'}
                      </Typography>
                    </Paper>
                    {response.sql && <Tooltip title="کپی SQL"><IconButton onClick={() => void navigator.clipboard.writeText(response.sql!)} sx={{ position: 'absolute', top: 8, right: 8, color: 'white', bgcolor: 'rgba(255,255,255,.08)' }}><ContentCopyRoundedIcon /></IconButton></Tooltip>}
                  </Box>
                  <Stack direction="row" gap={1} flexWrap="wrap" useFlexGap>
                    {Object.entries(response.parameters).map(([name, value]) => <Chip key={name} dir="ltr" label={`${name} = ${valueText(value)}`} />)}
                  </Stack>
                  {response.validation && <Alert severity={response.validation.valid ? 'success' : 'error'}>{response.validation.valid ? 'SQL از نظر syntax، schema و سیاست‌های ایمنی معتبر است.' : response.validation.errors.join('؛ ')}</Alert>}
                </Stack>
              )}
              {tab === 2 && (
                <Stack spacing={2.5}>
                  <Stack direction="row" gap={1} flexWrap="wrap" useFlexGap>
                    <Chip label={`Intent: ${response.analysis.intent}`} color="primary" />
                    <Chip label={`Mode: ${response.generation_mode ?? '—'}`} color="secondary" />
                    {response.llm_provider && <Chip label={`Provider: ${response.llm_provider}`} variant="outlined" />}
                    {response.llm_model && <Chip label={`Model: ${response.llm_model}`} variant="outlined" />}
                    <Chip label={`Schema: ${response.schema_version ?? 'active'}`} variant="outlined" />
                    {response.domain_plan?.matched_recipe_id && <Chip label={`Recipe: ${response.domain_plan.matched_recipe_id}`} />}
                  </Stack>
                  <Box><Typography variant="subtitle2" mb={1}>جدول‌های لازم</Typography><Stack direction="row" gap={1} flexWrap="wrap" useFlexGap>{response.domain_plan?.required_tables.map((table) => <Chip key={table} label={table} variant="outlined" dir="ltr" />)}</Stack></Box>
                  <Box><Typography variant="subtitle2" mb={1}>قواعد اعمال‌شده</Typography>{response.domain_plan?.business_rules.map((rule) => <Alert key={rule} severity="info" icon={false} sx={{ mb: 1 }}>{rule}</Alert>)}</Box>
                  {response.context_sources.length > 0 && <Box><Typography variant="subtitle2" mb={1}>منابع استفاده‌شده در context</Typography>{response.context_sources.map((source) => <Typography key={source} variant="body2" dir="ltr" textAlign="left" color="text.secondary">{source}</Typography>)}</Box>}
                  {response.retrieval_candidates.length > 0 && <Box><Typography variant="subtitle2" mb={1}>نامزدهای بازیابی</Typography>{response.retrieval_candidates.map((source) => <Typography key={source} variant="body2" dir="ltr" textAlign="left" color="text.secondary">{source}</Typography>)}</Box>}
                </Stack>
              )}
              {tab === 3 && (
                <Stack spacing={1.2}>
                  {response.trace.map((item, index) => (
                    <Paper key={`${item.stage}-${index}`} variant="outlined" sx={{ p: 1.5 }}>
                      <Stack direction="row" justifyContent="space-between" gap={2}>
                        <Box><Typography fontWeight={700} dir="ltr">{item.stage}</Typography><Typography variant="caption" color="text.secondary">{item.detail}</Typography></Box>
                        <Chip size="small" label={`${item.duration_ms} ms`} dir="ltr" />
                      </Stack>
                    </Paper>
                  ))}
                </Stack>
              )}
            </Box>
          </CardContent>
        </Card>
      )}
      <Dialog open={feedbackDialog} onClose={() => setFeedbackDialog(false)} fullWidth maxWidth="xs">
        <DialogTitle>علت نیاز به اصلاح</DialogTitle>
        <DialogContent>
          <FormControl fullWidth sx={{ mt: 1 }}>
            <InputLabel>دسته خطا</InputLabel>
            <Select value={feedbackReason} label="دسته خطا" onChange={(event) => setFeedbackReason(event.target.value)}>
              <MenuItem value="wrong_table">جدول اشتباه</MenuItem>
              <MenuItem value="wrong_join">رابطه یا Join اشتباه</MenuItem>
              <MenuItem value="wrong_filter">شرط یا فیلتر اشتباه</MenuItem>
              <MenuItem value="wrong_aggregation">محاسبه یا تجمیع اشتباه</MenuItem>
              <MenuItem value="missing_context">کمبود اطلاعات زمینه‌ای</MenuItem>
              <MenuItem value="access_issue">مشکل دسترسی</MenuItem>
              <MenuItem value="other">سایر</MenuItem>
            </Select>
          </FormControl>
          <Alert severity="info" sx={{ mt: 2 }}>هیچ متن آزاد، سؤال یا SQL در feedback ذخیره نمی‌شود.</Alert>
        </DialogContent>
        <DialogActions>
          <Button color="inherit" onClick={() => setFeedbackDialog(false)}>انصراف</Button>
          <Button variant="contained" color="error" onClick={() => void sendFeedback('not_helpful', feedbackReason)}>ثبت بازخورد</Button>
        </DialogActions>
      </Dialog>
    </>
  )
}
