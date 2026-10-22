import AccountTreeRoundedIcon from '@mui/icons-material/AccountTreeRounded'
import SearchRoundedIcon from '@mui/icons-material/SearchRounded'
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  InputAdornment,
  Paper,
  Stack,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tabs,
  TextField,
  Typography,
} from '@mui/material'
import ExpandMoreRoundedIcon from '@mui/icons-material/ExpandMoreRounded'
import { useEffect, useMemo, useState } from 'react'

import type { ApiClient } from '../api/client'
import type { SchemaGraph, SchemaOverview } from '../api/types'
import { PageHeader } from '../components/Common'

export function SchemaPage({ api, configured }: { api: ApiClient; configured: boolean }) {
  const [schema, setSchema] = useState<SchemaOverview | null>(null)
  const [graph, setGraph] = useState<SchemaGraph | null>(null)
  const [search, setSearch] = useState('')
  const [tab, setTab] = useState(0)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!configured) return
    let active = true
    setLoading(true)
    Promise.all([api.schema(), api.schemaGraph()])
      .then(([schemaResult, graphResult]) => {
        if (!active) return
        setSchema(schemaResult)
        setGraph(graphResult)
      })
      .catch((reason: Error) => active && setError(reason.message))
      .finally(() => active && setLoading(false))
    return () => {
      active = false
    }
  }, [api, configured])

  const tables = useMemo(() => {
    const query = search.trim().toLowerCase()
    if (!query) return schema?.tables ?? []
    return (schema?.tables ?? []).filter(
      (table) =>
        table.name.toLowerCase().includes(query) ||
        table.description.toLowerCase().includes(query) ||
        table.columns.some((column) => column.name.toLowerCase().includes(query)),
    )
  }, [schema, search])

  return (
    <>
      <PageHeader
        eyebrow="Schema Explorer"
        title="ساختار و روابط دیتابیس"
        description="جدول‌ها، ستون‌ها و FKهایی که نقش فعلی اجازه مشاهده و استفاده از آن‌ها را دارد."
        action={schema && <Stack direction="row" gap={1}><Chip label={`${schema.table_count} جدول`} color="primary" /><Chip label={`${schema.relationship_count} رابطه`} /></Stack>}
      />
      {!configured && <Alert severity="warning">برای دریافت schema ابتدا تنظیم اتصال را کامل کنید.</Alert>}
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {loading && <Box sx={{ display: 'grid', placeItems: 'center', py: 8 }}><CircularProgress /></Box>}
      {schema && (
        <Card>
          <CardContent sx={{ p: 0 }}>
            <Stack direction={{ xs: 'column', sm: 'row' }} alignItems={{ sm: 'center' }} justifyContent="space-between" gap={2} p={2.5}>
              <Tabs value={tab} onChange={(_, value: number) => setTab(value)}>
                <Tab label="جدول‌ها و ستون‌ها" />
                <Tab label="روابط FK" />
              </Tabs>
              {tab === 0 && (
                <TextField
                  size="small"
                  placeholder="جست‌وجوی جدول یا ستون"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  slotProps={{ input: { startAdornment: <InputAdornment position="start"><SearchRoundedIcon /></InputAdornment> } }}
                />
              )}
            </Stack>
            <Box sx={{ borderTop: '1px solid #E6EDF3', p: { xs: 1.5, md: 2.5 } }}>
              {tab === 0 && (
                <Stack spacing={1.2}>
                  {tables.map((table) => (
                    <Accordion key={table.name} disableGutters elevation={0} sx={{ border: '1px solid #E6EDF3', borderRadius: '12px !important', '&:before': { display: 'none' } }}>
                      <AccordionSummary expandIcon={<ExpandMoreRoundedIcon />}>
                        <Stack direction={{ xs: 'column', sm: 'row' }} alignItems={{ sm: 'center' }} gap={1.2} width="100%">
                          <Typography fontWeight={800} dir="ltr">{table.schema_name}.{table.name}</Typography>
                          <Chip size="small" label={`${table.columns.length} ستون`} />
                          <Typography variant="body2" color="text.secondary" noWrap flex={1}>{table.description || 'بدون توضیح کسب‌وکار'}</Typography>
                        </Stack>
                      </AccordionSummary>
                      <AccordionDetails>
                        {table.description && <Alert severity="info" icon={false} sx={{ mb: 2 }}>{table.description}</Alert>}
                        <Paper variant="outlined" sx={{ overflow: 'auto' }}>
                          <Table size="small">
                            <TableHead><TableRow><TableCell>ستون</TableCell><TableCell>نوع داده</TableCell><TableCell>Nullable</TableCell><TableCell>توضیح</TableCell></TableRow></TableHead>
                            <TableBody>{table.columns.map((column) => <TableRow key={column.name}><TableCell dir="ltr" sx={{ fontWeight: 700 }}>{column.name}</TableCell><TableCell dir="ltr">{column.data_type}</TableCell><TableCell>{column.nullable === null ? '—' : column.nullable ? 'بله' : 'خیر'}</TableCell><TableCell>{column.description || '—'}</TableCell></TableRow>)}</TableBody>
                          </Table>
                        </Paper>
                      </AccordionDetails>
                    </Accordion>
                  ))}
                  {tables.length === 0 && <Alert severity="info">موردی با این عبارت پیدا نشد.</Alert>}
                </Stack>
              )}
              {tab === 1 && (
                <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', xl: 'repeat(2, 1fr)' }, gap: 1.5 }}>
                  {(graph?.edges ?? []).map((edge) => (
                    <Paper key={edge.id} variant="outlined" sx={{ p: 2 }}>
                      <Stack direction="row" gap={1.5} alignItems="center">
                        <Box sx={{ width: 42, height: 42, borderRadius: 2.5, bgcolor: 'rgba(14,116,144,.1)', color: 'primary.main', display: 'grid', placeItems: 'center' }}><AccountTreeRoundedIcon /></Box>
                        <Box minWidth={0}>
                          <Typography fontWeight={800} dir="ltr" noWrap>{edge.source} → {edge.target}</Typography>
                          <Typography variant="body2" color="text.secondary" dir="ltr" noWrap>{edge.source_column} → {edge.target_column}</Typography>
                          <Typography variant="caption" color="text.secondary" dir="ltr">{edge.id}</Typography>
                        </Box>
                      </Stack>
                    </Paper>
                  ))}
                </Box>
              )}
            </Box>
          </CardContent>
        </Card>
      )}
    </>
  )
}
