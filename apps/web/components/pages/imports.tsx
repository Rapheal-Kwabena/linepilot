'use client';

import { useMemo, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Check, Download, FileInput, Upload } from 'lucide-react';
import { api, downloadUrl } from '@/lib/api';
import { csvEncode } from '@/lib/format';
import { useWorkspace } from '@/components/workspace-context';
import { Button, ErrorNotice, PageHeader, Panel } from '@/components/ui';

type Category = 'jobs' | 'operations' | 'machines' | 'downtime';
const fields: Record<Category, { required: string[]; optional: string[] }> = {
  jobs: { required: ['job_number', 'product_name', 'quantity', 'priority', 'release_time', 'due_time', 'operation_number', 'operation_name', 'machine_code', 'processing_minutes'], optional: ['customer_reference'] },
  operations: { required: ['job_number', 'operation_number', 'operation_name', 'machine_code', 'processing_minutes'], optional: ['setup_group'] },
  machines: { required: ['machine_code', 'machine_name', 'work_center_code'], optional: ['machine_type', 'hourly_operating_cost', 'active'] },
  downtime: { required: ['machine_code', 'start_time', 'end_time'], optional: ['reason', 'event_type'] },
};

function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [], cell = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"') { if (quoted && text[i + 1] === '"') { cell += '"'; i++; } else quoted = !quoted; }
    else if (c === ',' && !quoted) { row.push(cell); cell = ''; }
    else if ((c === '\n' || c === '\r') && !quoted) { if (c === '\r' && text[i + 1] === '\n') i++; row.push(cell); if (row.some((value) => value.trim())) rows.push(row); row = []; cell = ''; }
    else cell += c;
  }
  row.push(cell); if (row.some((value) => value.trim())) rows.push(row);
  return rows;
}

type Preview = { category: string; detected_columns: string[]; row_count: number; valid: boolean; errors: { row: number; field: string; message: string }[]; preview: Record<string, unknown>[]; import_behavior: string };
type ImportResult = { id: string; import_status: string; rows_processed: number; rows_failed: number; validation_errors?: unknown[] };

export function ImportsPage() {
  const { bootstrap, refresh } = useWorkspace();
  const [category, setCategory] = useState<Category>('jobs');
  const [fileName, setFileName] = useState('');
  const [raw, setRaw] = useState('');
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [step, setStep] = useState(0);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const parsed = useMemo(() => parseCsv(raw), [raw]);
  const headers = useMemo(() => parsed[0] || [], [parsed]);
  const canonicalFields = useMemo(() => [...fields[category].required, ...fields[category].optional], [category]);
  const mappedCsv = useMemo(() => { const header = canonicalFields.filter((field) => mapping[field]); const lines = parsed.slice(1).map((row) => header.map((field) => csvEncode(row[headers.indexOf(mapping[field])] ?? '')).join(',')); return [header.join(','), ...lines].join('\n'); }, [canonicalFields, mapping, parsed, headers]);
  const missing = fields[category].required.filter((field) => !mapping[field]);
  const previewMutation = useMutation({ mutationFn: () => api.previewImport({ facility_id: bootstrap.facility.id, category, csv_text: mappedCsv, file_name: fileName }) as Promise<Preview>, onSuccess: (data) => { setPreview(data); setStep(2); }, onError: setActionError });
  const confirmMutation = useMutation({ mutationFn: () => api.confirmImport({ facility_id: bootstrap.facility.id, category, csv_text: mappedCsv, file_name: fileName }) as Promise<ImportResult>, onSuccess: async (data) => { setResult(data); setStep(3); await refresh(); }, onError: setActionError });
  const chooseFile = async (file?: File) => {
    if (!file) return;
    if (file.size > 2_000_000) { setActionError(new Error('CSV must be 2 MB or smaller.')); return; }
    const content = await file.text();
    const columns = parseCsv(content)[0] || [];
    const next: Record<string, string> = {};
    for (const field of [...fields[category].required, ...fields[category].optional]) { const match = columns.find((column) => column.trim().toLowerCase().replaceAll(' ', '_') === field); if (match) next[field] = match; }
    setRaw(content); setFileName(file.name); setMapping(next); setPreview(null); setResult(null); setStep(1); setActionError(null);
  };
  const downloadTemplate = async () => { try { await downloadUrl(api.importTemplateUrl(category), `linepilot-${category}-template.csv`); } catch (error) { setActionError(error); } };
  const reset = () => { setRaw(''); setFileName(''); setMapping({}); setPreview(null); setResult(null); setStep(0); setActionError(null); };
  return <><PageHeader index="10" title="Data Imports" subtitle="Map, validate and commit production CSV files as a single transaction."><Button onClick={downloadTemplate}><Download />Download template</Button></PageHeader>{actionError && <ErrorNotice error={actionError} retry={() => setActionError(null)} />}
    <Panel title="Import production data" meta="All-or-nothing validation / no partial writes"><div className="stepper">{['SELECT FILE', 'MAP COLUMNS', 'VALIDATE', 'CONFIRM'].map((label, i) => <div className={`step ${step === i ? 'active' : step > i ? 'done' : ''}`} key={label}>{String(i + 1).padStart(2, '0')} / {label}</div>)}</div><div className="panel-body">
      {step === 0 && <div className="stack"><div className="field" style={{ maxWidth: 260 }}><label htmlFor="import-category">Import category</label><select id="import-category" value={category} onChange={(e) => setCategory(e.target.value as Category)}><option value="jobs">Production jobs and operations</option><option value="operations">Operations for existing jobs</option><option value="machines">Machine resources</option><option value="downtime">Machine downtime</option></select></div><label className="import-drop"><FileInput size={27} strokeWidth={1.4} /><strong>Select a CSV file</strong><span>Maximum 2 MB · UTF-8 · header row required</span><input type="file" accept=".csv,text/csv" onChange={(e) => chooseFile(e.target.files?.[0])} /></label><div className="surface-note">Jobs CSV rows define eligible machine options for new operations. Operations CSV appends new operation numbers to existing planned or released jobs. Repeated job and operation fields are grouped. Timestamps must include a timezone offset.</div></div>}
      {step === 1 && <div className="stack"><div><strong>{fileName}</strong><div className="tiny muted">{Math.max(0, parsed.length - 1)} data rows · {headers.length} source columns</div></div><div className="data-table-wrap"><table className="data-table"><thead><tr><th>Required field</th><th>CSV column</th></tr></thead><tbody>{canonicalFields.map((field) => <tr key={field}><td className="mono">{field} {fields[category].required.includes(field) ? '*' : ''}</td><td><select className="control" value={mapping[field] || ''} onChange={(e) => setMapping((current) => ({ ...current, [field]: e.target.value }))} aria-label={`Map ${field}`}><option value="">Do not map</option>{headers.map((header, index) => <option key={`${header}-${index}`} value={header}>{header}</option>)}</select></td></tr>)}</tbody></table></div>{missing.length > 0 && <div className="field-error">Required fields missing: {missing.join(', ')}</div>}<div><Button onClick={() => setStep(0)}>Back</Button> <Button variant="primary" onClick={() => previewMutation.mutate()} disabled={missing.length > 0 || previewMutation.isPending}>{previewMutation.isPending ? 'Validating…' : 'Validate import'}</Button></div></div>}
      {step === 2 && preview && <div className="stack"><div className={`callout ${preview.valid ? '' : 'critical-text'}`}>{preview.valid ? `Validation passed for ${preview.row_count} rows.` : `Validation found ${preview.errors.length} row-level errors.`} Import behavior: {preview.import_behavior.replaceAll('_', ' ')}.</div>{preview.errors.length > 0 && <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Row</th><th>Field</th><th>Error</th></tr></thead><tbody>{preview.errors.map((error, i) => <tr key={`${error.row}-${error.field}-${i}`}><td className="mono">{error.row}</td><td className="mono">{error.field}</td><td className="critical-text">{error.message}</td></tr>)}</tbody></table></div>}<div><h3 style={{ marginBottom: 8 }}>Parsed preview</h3><div className="data-table-wrap"><table className="data-table"><thead><tr>{preview.detected_columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{preview.preview.map((row, i) => <tr key={i}>{preview.detected_columns.map((column) => <td key={column} className="mono">{String(row[column] ?? '')}</td>)}</tr>)}</tbody></table></div></div><div><Button onClick={() => setStep(1)}>Edit mapping</Button> <Button variant="primary" onClick={() => confirmMutation.mutate()} disabled={!preview.valid || confirmMutation.isPending}><Upload />{confirmMutation.isPending ? 'Importing…' : 'Confirm import'}</Button></div></div>}
      {step === 3 && result && <div className="stack"><div className="callout"><Check size={15} /> Import {result.import_status}: {result.rows_processed} rows processed, {result.rows_failed} rejected.</div><div className="mono tiny">BATCH {result.id.slice(0, 8).toUpperCase()}</div><div><Button variant="primary" onClick={reset}>Import another file</Button></div></div>}
    </div></Panel>
  </>;
}
