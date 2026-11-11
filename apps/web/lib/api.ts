import type { Bootstrap, Facility, ListResponse, Machine, OperatingCalendar, ProductionJob, Report, Scenario, ScheduleRun, SimulationRun, WorkCenter } from './types';

export class ApiError extends Error {
  constructor(message: string, public status: number, public detail?: unknown) { super(message); this.name = 'ApiError'; }
}

const prefix = '/api/v1';

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${prefix}${path}`, { ...options, headers, cache: 'no-store' });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    const message = typeof detail?.error?.message === 'string' ? detail.error.message : typeof detail?.detail === 'string' ? detail.detail : typeof detail?.message === 'string' ? detail.message : `${response.status} ${response.statusText}`;
    throw new ApiError(message, response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function json(method: string, body: unknown): RequestInit { return { method, body: JSON.stringify(body) }; }

export const api = {
  bootstrap: (facilityId?: string | null) => request<Bootstrap>(`/bootstrap${facilityId ? `?facility_id=${encodeURIComponent(facilityId)}` : ''}`),
  facilities: () => request<ListResponse<Facility> | Facility[]>('/facilities'),
  workCenters: () => request<ListResponse<WorkCenter> | WorkCenter[]>('/work-centers'),
  createWorkCenter: (body: unknown) => request<WorkCenter>('/work-centers', json('POST', body)),
  calendars: () => request<ListResponse<OperatingCalendar>>('/calendars'),
  createCalendar: (body: unknown) => request<OperatingCalendar>('/calendars', json('POST', body)),
  updateCalendar: (id: string, body: unknown) => request<OperatingCalendar>(`/calendars/${id}`, json('PATCH', body)),
  machines: (params = '') => request<ListResponse<Machine>>(`/machines${params}`),
  createMachine: (body: unknown) => request<Machine>('/machines', json('POST', body)),
  updateMachine: (id: string, body: unknown) => request<Machine>(`/machines/${id}`, json('PATCH', body)),
  createDowntime: (body: unknown) => request<import('./types').Downtime>('/downtime', json('POST', body)),
  deleteDowntime: (id: string) => request<void>(`/downtime/${id}`, { method: 'DELETE' }),
  jobs: (params = '') => request<ListResponse<ProductionJob>>(`/jobs${params}`),
  createJob: (body: unknown) => request<ProductionJob>('/jobs', json('POST', body)),
  updateJob: (id: string, body: unknown) => request<ProductionJob>(`/jobs/${id}`, json('PATCH', body)),
  duplicateJob: (id: string) => request<ProductionJob>(`/jobs/${id}/duplicate`, { method: 'POST' }),
  deleteJob: (id: string) => request<void>(`/jobs/${id}`, { method: 'DELETE' }),
  schedules: (facilityId?: string) => request<ListResponse<ScheduleRun>>(`/schedules${facilityId ? `?facility_id=${encodeURIComponent(facilityId)}` : ''}`),
  schedule: (id: string) => request<ScheduleRun>(`/schedules/${id}`),
  createSchedule: (body: unknown) => request<ScheduleRun>('/schedules', json('POST', body)),
  cancelSchedule: (id: string) => request<ScheduleRun>(`/schedules/${id}/cancel`, { method: 'POST' }),
  scenarios: (facilityId?: string) => request<ListResponse<Scenario>>(`/scenarios${facilityId ? `?facility_id=${encodeURIComponent(facilityId)}` : ''}`),
  createScenario: (body: unknown) => request<Scenario>('/scenarios', json('POST', body)),
  evaluateScenario: (id: string, body: unknown) => request<ScheduleRun>(`/scenarios/${id}/evaluate`, json('POST', body)),
  simulation: (id: string) => request<SimulationRun>(`/simulations/${id}`),
  simulationMetrics: (id: string) => request<{ simulation_id: string; status: string; metrics: Record<string, unknown> }>(`/simulations/${id}/metrics`),
  simulationEvents: (id: string) => request<ListResponse<import('./types').SimulationEvent>>(`/simulations/${id}/events?page_size=1000`),
  createSimulation: (body: unknown) => request<SimulationRun>('/simulations', json('POST', body)),
  simulations: (scheduleId: string) => request<ListResponse<SimulationRun>>(`/simulations?schedule_run_id=${encodeURIComponent(scheduleId)}`),
  cancelSimulation: (id: string) => request<SimulationRun>(`/simulations/${id}/cancel`, { method: 'POST' }),
  bottlenecks: (id: string) => request<unknown>(`/analytics/bottlenecks?schedule_id=${encodeURIComponent(id)}`),
  utilization: (id: string, startMin?: number, endMin?: number) => {
    const params = new URLSearchParams({ schedule_id: id });
    if (startMin !== undefined) params.set('start_min', String(startMin));
    if (endMin !== undefined) params.set('end_min', String(endMin));
    return request<unknown>(`/analytics/utilization?${params}`);
  },
  throughput: (id: string) => request<unknown>(`/analytics/throughput?schedule_id=${encodeURIComponent(id)}`),
  previewImport: (body: unknown) => request<unknown>('/imports/preview', json('POST', body)),
  confirmImport: (body: unknown) => request<unknown>('/imports', json('POST', body)),
  createReport: (body: unknown) => request<Report>('/reports', json('POST', body)),
  reports: (facilityId: string) => request<ListResponse<Report>>(`/reports?facility_id=${encodeURIComponent(facilityId)}`),
  report: (id: string) => request<Report>(`/reports/${id}`),
  reportDownloadUrl: (id: string, format: 'csv' | 'pdf') => `${prefix}/reports/${id}/download?format=${format}`,
  importTemplateUrl: (category: string) => `${prefix}/imports/templates/${encodeURIComponent(category)}`,
};

export function toItems<T>(value: ListResponse<T> | T[] | undefined): T[] { return Array.isArray(value) ? value : value?.items || []; }

export async function downloadUrl(url: string, fileName: string) {
  const response = await fetch(url);
  if (!response.ok) throw new ApiError(`Download failed: ${response.status}`, response.status);
  const blob = await response.blob();
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = href;
  anchor.download = fileName;
  anchor.click();
  URL.revokeObjectURL(href);
}

export function downloadText(text: string, fileName: string, mime = 'text/csv') {
  const href = URL.createObjectURL(new Blob([text], { type: mime }));
  const anchor = document.createElement('a');
  anchor.href = href;
  anchor.download = fileName;
  anchor.click();
  URL.revokeObjectURL(href);
}
