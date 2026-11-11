'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { ArrowRight, AlertTriangle, Download, RefreshCcw } from 'lucide-react';
import { api, downloadText } from '@/lib/api';
import { csvEscape, dateTime, getDisplayTimeZone, isoToLocalInput, localInputToIso, metric, minutes, number, percent } from '@/lib/format';
import { displayJobs, displayMachines } from '@/lib/snapshot';
import { useWorkspace } from '@/components/workspace-context';
import { Button, Empty, ErrorNotice, Metric, PageHeader, Panel, Status } from '@/components/ui';
import { GanttChart } from '@/components/scheduling/gantt';

type Utilization = {
  machine_id: string;
  machine_code?: string;
  utilization: number | null;
  busy_minutes?: number;
  available_minutes?: number;
};

function readUtilization(data: unknown): Utilization[] {
  if (Array.isArray(data)) return data;
  if (data && typeof data === 'object') {
    const obj = data as Record<string, unknown>;
    const values = obj.items || obj.machines || obj.utilization;
    if (Array.isArray(values)) return values;
  }
  return [];
}

function utilizationFraction(value: number | null): number {
  const numeric = value ?? 0;
  return numeric > 1 ? numeric / 100 : numeric;
}

export function OverviewPage() {
  const { bootstrap, selectedSchedule, selectedScheduleId, schedules, selectSchedule, refresh } = useWorkspace();
  const [rangeStart, setRangeStart] = useState('');
  const [rangeEnd, setRangeEnd] = useState('');
  const [machineFilter, setMachineFilter] = useState('all');
  const utilizationQuery = useQuery({
    queryKey: ['utilization', selectedScheduleId],
    queryFn: () => api.utilization(selectedScheduleId!),
    enabled: !!selectedScheduleId && selectedSchedule?.status === 'completed',
  });

  const assignments = selectedSchedule?.assignments || [];
  const origin = selectedSchedule?.origin || selectedSchedule?.snapshot?.origin;
  const lastMinute = Math.max(1, ...assignments.map((assignment) => assignment.end_min));
  const machines = useMemo(() => displayMachines(selectedSchedule, bootstrap.machines), [selectedSchedule, bootstrap.machines]);
  const jobs = useMemo(() => displayJobs(selectedSchedule, bootstrap.jobs), [selectedSchedule, bootstrap.jobs]);

  useEffect(() => {
    setMachineFilter('all');
    if (!origin) {
      setRangeStart('');
      setRangeEnd('');
      return;
    }
    setRangeStart(isoToLocalInput(origin));
    setRangeEnd(isoToLocalInput(new Date(new Date(origin).getTime() + lastMinute * 60_000).toISOString()));
  }, [selectedSchedule?.id, origin, lastMinute]);

  const selectedRange = useMemo(() => {
    if (!origin || !rangeStart || !rangeEnd) return { start: 0, end: lastMinute, error: '' };
    try {
      const originMs = new Date(origin).getTime();
      const start = Math.max(0, (new Date(localInputToIso(rangeStart)).getTime() - originMs) / 60_000);
      const end = Math.min(lastMinute, (new Date(localInputToIso(rangeEnd)).getTime() - originMs) / 60_000);
      if (end <= start) return { start, end, error: 'Choose a range that overlaps the selected schedule and has an end after its start.' };
      return { start, end, error: '' };
    } catch (error) {
      return { start: 0, end: lastMinute, error: error instanceof Error ? error.message : 'Enter a valid date range.' };
    }
  }, [origin, rangeStart, rangeEnd, lastMinute]);

  const visibleAssignments = assignments.filter((assignment) =>
    (machineFilter === 'all' || assignment.machine_id === machineFilter)
    && assignment.end_min > selectedRange.start
    && assignment.start_min < selectedRange.end,
  );

  const exportTimeline = () => {
    if (!selectedSchedule || !origin || selectedRange.error) return;
    const jobById = new Map(jobs.map((job) => [job.id, job]));
    const machineById = new Map(machines.map((machine) => [machine.id, machine]));
    const rows: unknown[][] = [
      ['LINEPILOT production activity timeline'],
      ['Schedule run', selectedSchedule.id],
      ['Algorithm', selectedSchedule.algorithm],
      ['Visible from (UTC)', new Date(new Date(origin).getTime() + selectedRange.start * 60_000).toISOString()],
      ['Visible to (UTC)', new Date(new Date(origin).getTime() + selectedRange.end * 60_000).toISOString()],
      [],
      ['Job', 'Operation', 'Machine', 'Start (UTC)', 'End (UTC)', 'Duration (min)'],
    ];
    for (const assignment of visibleAssignments) {
      const job = jobById.get(assignment.job_id);
      const operation = job?.operations.find((item) => item.id === assignment.operation_id);
      rows.push([
        job?.job_number || assignment.job_id,
        operation ? `${operation.operation_number} · ${operation.operation_name}` : assignment.operation_id,
        machineById.get(assignment.machine_id)?.machine_code || assignment.machine_id,
        assignment.start_time,
        assignment.end_time,
        assignment.end_min - assignment.start_min,
      ]);
    }
    downloadText(rows.map((row) => row.map(csvEscape).join(',')).join('\r\n'), `linepilot-timeline-${selectedSchedule.id.slice(0, 8)}.csv`);
  };

  const metrics = selectedSchedule?.metrics;
  const lateJobs = metric(metrics, 'late_jobs');
  const ranked = readUtilization(utilizationQuery.data).sort((a, b) => (b.utilization ?? 0) - (a.utilization ?? 0));
  const kpiPeriod = origin && assignments.length
    ? `${dateTime(origin)} – ${dateTime(new Date(new Date(origin).getTime() + lastMinute * 60_000).toISOString())}`
    : 'Awaiting a completed schedule';

  return <>
    <PageHeader index="01" title="Operations Overview" subtitle="Production capacity, schedule performance and operational constraints.">
      <Button onClick={() => refresh()}><RefreshCcw />Refresh</Button>
      <Link className="button primary" href="/scheduling">New optimization<ArrowRight size={16} /></Link>
    </PageHeader>

    <div className="toolbar">
      <span className="toolbar-label">Facility</span>
      <span className="tag">{bootstrap.facility.facility_name}</span>
      <span className="toolbar-label" style={{ marginLeft: 10 }}>Active schedule</span>
      <select className="control" style={{ width: 240 }} value={selectedScheduleId || ''} onChange={(event) => selectSchedule(event.target.value || null)} aria-label="Active schedule">
        <option value="">No schedule</option>
        {schedules.map((run) => <option key={run.id} value={run.id}>{run.algorithm.toUpperCase()} · {dateTime(run.created_at)} · {run.status}</option>)}
      </select>
      <span className="toolbar-spacer" />
      <span className="tiny muted">Source: {bootstrap.facility.sample_data ? 'fictional sample data' : 'persisted production data'} and saved calculations</span>
    </div>

    <div className="metric-grid">
      <Metric label="Scheduled jobs" value={number(metric(metrics, 'scheduled_jobs', 'completed_jobs') ?? (assignments.length ? new Set(assignments.map((assignment) => assignment.job_id)).size : undefined))} unit="jobs" context="Full selected schedule" />
      <Metric label="Production throughput" value={number(metric(metrics, 'throughput_jobs_per_hour', 'throughput'), 2)} unit="jobs/h" context="Full selected schedule" />
      <Metric label="Machine utilization" value={percent(metric(metrics, 'average_machine_utilization'))} context="Busy / available time" />
      <Metric label="On-time delivery" value={percent(metric(metrics, 'on_time_delivery_rate', 'on_time_rate'))} context="Jobs meeting due date" />
      <Metric label="Avg. job waiting" value={minutes(metric(metrics, 'mean_job_waiting_minutes'))} context="Flow minus processing" />
      <Metric label="Scheduled makespan" value={minutes(metric(metrics, 'makespan_minutes', 'makespan'))} context="From schedule origin" />
    </div>

    <div className="stack">
      <Panel title="Production activity timeline" meta={selectedSchedule ? `${selectedSchedule.algorithm.toUpperCase()} · ${selectedSchedule.solver_status || selectedSchedule.status} · ${visibleAssignments.length} visible operations` : 'Awaiting first schedule'} action={<><Button onClick={exportTimeline} disabled={!visibleAssignments.length || !!selectedRange.error}><Download />Export timeline CSV</Button><Link className="button small" href="/scheduling">Open studio<ArrowRight size={13} /></Link></>}>
        {selectedSchedule && <div className="toolbar" style={{ padding: '12px 16px' }}>
          <label className="field" style={{ margin: 0 }}><span className="field-label">From / {getDisplayTimeZone()}</span><input className="control" type="datetime-local" value={rangeStart} onChange={(event) => setRangeStart(event.target.value)} /></label>
          <label className="field" style={{ margin: 0 }}><span className="field-label">To / {getDisplayTimeZone()}</span><input className="control" type="datetime-local" value={rangeEnd} onChange={(event) => setRangeEnd(event.target.value)} /></label>
          <label className="field" style={{ margin: 0 }}><span className="field-label">Machine</span><select className="control" value={machineFilter} onChange={(event) => setMachineFilter(event.target.value)}><option value="all">All machines</option>{machines.map((machine) => <option key={machine.id} value={machine.id}>{machine.machine_code}</option>)}</select></label>
          <span className="tiny muted">KPI period: {kpiPeriod}</span>
        </div>}
        {selectedRange.error ? <div className="panel-body"><div className="field-error" role="alert">{selectedRange.error}</div></div> : <GanttChart schedule={selectedSchedule} machines={bootstrap.machines} jobs={bootstrap.jobs} compact machineFilter={machineFilter} rangeStartMin={selectedRange.start} rangeEndMin={selectedRange.end} />}
      </Panel>

      <div className="grid-2">
        <Panel title="Machine availability" meta="Scheduled utilization by machine"><div className="panel-body">
          {utilizationQuery.error && <ErrorNotice error={utilizationQuery.error} retry={() => utilizationQuery.refetch()} />}
          {ranked.length ? <div className="rank-list">{ranked.slice(0, 6).map((row) => {
            const machine = machines.find((item) => item.id === row.machine_id);
            const fraction = utilizationFraction(row.utilization);
            return <div className="rank-row" key={row.machine_id}><span>{machine?.machine_code || row.machine_code || row.machine_id.slice(0, 6)}</span><div className="rank-track" title={`${number(row.busy_minutes)} busy minutes / ${number(row.available_minutes)} available minutes`}><div className={`rank-fill ${fraction >= .85 ? 'high' : ''}`} style={{ width: `${Math.max(0, Math.min(100, fraction * 100))}%` }} /></div><strong>{percent(row.utilization)}</strong></div>;
          })}</div> : <Empty title="NO UTILIZATION DATA" text="Utilization becomes available after a schedule completes." />}
        </div></Panel>

        <Panel title="Operations requiring attention" meta="Findings from the selected run"><div className="panel-body"><ul className="attention-list">
          {lateJobs !== undefined && lateJobs > 0 && <li><AlertTriangle size={15} /><div><strong>{number(lateJobs)} jobs finish after their due date</strong><span>Review deadlines or evaluate a capacity scenario.</span></div></li>}
          {ranked.filter((row) => utilizationFraction(row.utilization) >= .85).map((row) => <li key={row.machine_id}><AlertTriangle size={15} /><div><strong>{machines.find((machine) => machine.id === row.machine_id)?.machine_code || row.machine_code || row.machine_id.slice(0, 6)} has high scheduled utilization</strong><span>{percent(row.utilization)} of available time is occupied; inspect queueing before calling it a bottleneck.</span></div></li>)}
          {selectedSchedule?.status === 'failed' && <li><AlertTriangle size={15} /><div><strong>Scheduling failed</strong><span>Inspect run diagnostics in Scheduling Studio.</span></div></li>}
          {selectedSchedule?.status === 'completed' && (!lateJobs || lateJobs <= 0) && !ranked.some((row) => utilizationFraction(row.utilization) >= .85) && <li><div><strong>No current exceptions from this schedule</strong><span>Run scenarios to test sensitivity to disruptions.</span></div></li>}
          {!selectedSchedule && <li><div><strong>Awaiting first schedule</strong><span>Generate a schedule to identify delivery and capacity exceptions.</span></div></li>}
        </ul></div></Panel>
      </div>

      <Panel title="Schedule status" meta="Latest selected engineering run"><div className="panel-body">
        {selectedSchedule ? <div className="grid-3"><div><div className="section-kicker">ALGORITHM</div><strong className="mono">{selectedSchedule.algorithm.toUpperCase()}</strong></div><div><div className="section-kicker">SOLVER</div><Status value={selectedSchedule.solver_status || selectedSchedule.status} /></div><div><div className="section-kicker">ENGINE RUNTIME</div><strong className="mono">{number(selectedSchedule.duration_ms ?? selectedSchedule.runtime_ms)} ms</strong></div></div> : <Empty title="AWAITING OPTIMIZATION" text="Generate an FCFS or CP-SAT run to populate the workstation with measured results." />}
      </div></Panel>
    </div>
  </>;
}
