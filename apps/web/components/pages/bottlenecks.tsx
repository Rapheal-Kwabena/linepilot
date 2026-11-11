'use client';

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, ArrowRight, RefreshCcw } from 'lucide-react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { dateTime, minutes, number, percent } from '@/lib/format';
import { useWorkspace } from '@/components/workspace-context';
import { EngineeringChart } from '@/components/analytics/engineering-chart';
import { Button, Empty, ErrorNotice, PageHeader, Panel, Status } from '@/components/ui';

type MachineRanking = {
  machine_id: string;
  machine_code: string;
  work_center_id: string;
  busy_minutes: number;
  available_minutes: number;
  idle_minutes: number;
  downtime_minutes: number;
  calendar_closed_minutes: number;
  utilization: number | null;
  operating_cost: number;
  scheduled_operation_count: number;
  late_jobs_using_machine: number;
  mean_queue_wait_minutes: number | null;
  max_queue_length: number | null;
};
type Finding = { type: string; machine_id?: string; title: string; evidence: string | Record<string, unknown>; interpretation?: string };
type CenterThroughput = { work_center_id: string; completed_operations: number; operations_per_hour: number | null };
type LateJob = { job_id: string; job_number: string; completion_min: number; due_min: number; tardiness_minutes: number };
type BottleneckResponse = {
  schedule_id: string;
  status: string;
  simulation_id?: string | null;
  machine_rankings: MachineRanking[];
  findings: Finding[];
  work_center_throughput: CenterThroughput[];
  late_jobs: LateJob[];
};
type WipPoint = { time_min: number; wip: number };

function evidenceText(evidence: Finding['evidence']) {
  if (typeof evidence === 'string') return evidence;
  return Object.entries(evidence).map(([key, value]) => `${key.replaceAll('_', ' ')}: ${key.includes('utilization') ? percent(value) : typeof value === 'number' ? number(value, 1) : String(value)}`).join(' · ');
}

export function BottlenecksPage() {
  const { bootstrap, selectedScheduleId, selectedSchedule, schedules, selectSchedule } = useWorkspace();
  const [sort, setSort] = useState<'utilization' | 'mean_queue_wait_minutes' | 'late_jobs_using_machine'>('utilization');
  const query = useQuery({
    queryKey: ['bottlenecks', selectedScheduleId],
    queryFn: () => api.bottlenecks(selectedScheduleId!) as Promise<BottleneckResponse>,
    enabled: !!selectedScheduleId && selectedSchedule?.status === 'completed',
  });
  const data = query.data;
  const simulationId = data?.simulation_id;
  const simulationQuery = useQuery({
    queryKey: ['simulation-metrics', simulationId],
    queryFn: () => api.simulationMetrics(simulationId!),
    enabled: !!simulationId,
  });
  const rankings = useMemo(() => (data?.machine_rankings || []).slice().sort((a, b) => (b[sort] || 0) - (a[sort] || 0)), [data, sort]);
  const downtimeRows = useMemo(() => rankings.filter((row) => row.downtime_minutes > 0).slice().sort((a, b) => b.downtime_minutes - a.downtime_minutes), [rankings]);
  const lateJobs = data?.late_jobs || [];
  const centerRows = data?.work_center_throughput || [];
  const simulationMetrics = simulationQuery.data?.metrics;
  const rawWip = simulationMetrics?.wip_time_series;
  const wipPoints = useMemo<WipPoint[]>(() => Array.isArray(rawWip)
    ? rawWip.filter((point): point is WipPoint => point && typeof point.time_min === 'number' && typeof point.wip === 'number')
    : [], [rawWip]);
  const replicationMetrics = simulationMetrics?.replication_metrics;
  const replicationCount = Array.isArray(replicationMetrics) ? replicationMetrics.length : 1;
  const wipChart = useMemo(() => ({
    grid: { left: 55, right: 22, top: 35, bottom: 55 },
    xAxis: { type: 'value' as const, name: 'Minute from origin', min: 0 },
    yAxis: { type: 'value' as const, name: 'Jobs in process', min: 0, minInterval: 1 },
    tooltip: { trigger: 'axis' as const },
    series: [{ name: 'WIP', type: 'line' as const, step: 'end' as const, showSymbol: false, lineStyle: { color: 'var(--action)', width: 2 }, itemStyle: { color: 'var(--action)' }, data: wipPoints.map((point) => [point.time_min, point.wip]) }],
  }), [wipPoints]);
  const utilizationChart = useMemo(() => ({
    grid: { left: 112, right: 28, top: 24, bottom: 35 },
    xAxis: { type: 'value' as const, name: 'Utilization / %', max: 100, axisLabel: { formatter: '{value}%' } },
    yAxis: { type: 'category' as const, data: rankings.slice().reverse().map((row) => row.machine_code), axisTick: { show: false } },
    tooltip: { trigger: 'axis' as const, axisPointer: { type: 'shadow' as const }, valueFormatter: (value: unknown) => `${number(Number(value), 1)}%` },
    series: [{ name: 'Scheduled utilization', type: 'bar' as const, data: rankings.slice().reverse().map((row) => row.utilization === null ? null : Math.round((row.utilization > 1 ? row.utilization : row.utilization * 100) * 10) / 10), itemStyle: { color: 'var(--action)' } }],
  }), [rankings]);

  return <>
    <PageHeader index="07" title="Bottleneck Analysis" subtitle="Separate high utilization from queues, tardiness and capacity limits.">
      <Button onClick={() => query.refetch()} disabled={!selectedScheduleId}><RefreshCcw />Refresh analysis</Button>
      <Link className="button primary" href="/scenarios">Test a scenario<ArrowRight size={16} /></Link>
    </PageHeader>
    <div className="toolbar"><span className="toolbar-label">Schedule</span><select className="control" style={{ width: 285 }} value={selectedScheduleId || ''} onChange={(event) => selectSchedule(event.target.value || null)} aria-label="Select schedule for bottleneck analysis"><option value="">Choose completed schedule</option>{schedules.filter((run) => run.status === 'completed').map((run) => <option value={run.id} key={run.id}>{run.algorithm.toUpperCase()} · {dateTime(run.created_at)}</option>)}</select><span className="toolbar-spacer" />{selectedSchedule && <Status value={selectedSchedule.solver_status || selectedSchedule.status} />}</div>
    {query.error && <ErrorNotice error={query.error} retry={() => query.refetch()} />}
    {!selectedSchedule || selectedSchedule.status !== 'completed' ? <Panel title="Analysis unavailable"><Empty title="SELECT A COMPLETED SCHEDULE" text="Bottleneck findings require saved machine assignments and calculated metrics." /></Panel> : <div className="stack">
      <div className="grid-2">
        <Panel title="Utilization ranking" meta="Busy processing time / available operating time"><div className="panel-body">{rankings.length ? <><EngineeringChart option={utilizationChart} label={`Machine utilization: ${rankings.map((row) => `${row.machine_code} ${percent(row.utilization)}`).join(', ')}`} height={300} /><p className="tiny muted" style={{ marginBottom: 0 }}>High utilization is capacity pressure, not proof of a throughput bottleneck. Compare queue wait and late jobs below.</p></> : <Empty title="NO RANKING" text="The selected run has no machine metrics." />}</div></Panel>
        <Panel title="Explainable findings" meta="Derived from saved schedule and simulation data"><div className="panel-body">{data?.findings?.length ? <ul className="attention-list">{data.findings.map((finding, index) => <li key={`${finding.type}-${finding.machine_id}-${index}`}><AlertTriangle size={15} /><div><strong>{finding.title}</strong><span>{evidenceText(finding.evidence)}</span>{finding.interpretation && <div className="tiny muted" style={{ marginTop: 5 }}>{finding.interpretation}</div>}</div></li>)}</ul> : <Empty title="NO FINDINGS" text="No attention conditions were detected under the current analytical rules." />}</div></Panel>
      </div>
      <Panel title="Machine constraint evidence" meta="Queue and delivery exposure for each resource" action={<select className="control" style={{ width: 195 }} value={sort} onChange={(event) => setSort(event.target.value as typeof sort)} aria-label="Sort machine evidence"><option value="utilization">Sort: utilization</option><option value="mean_queue_wait_minutes">Sort: mean queue wait</option><option value="late_jobs_using_machine">Sort: late jobs using machine</option></select>}><div className="data-table-wrap"><table className="data-table"><thead><tr><th>Machine</th><th>Work center</th><th>Utilization</th><th>Busy</th><th>Available</th><th>Mean queue wait</th><th>Max queue</th><th>Late jobs using</th><th>Downtime</th><th>Operating cost</th></tr></thead><tbody>{rankings.map((row) => <tr key={row.machine_id}><td className="mono"><strong>{row.machine_code}</strong></td><td>{bootstrap.work_centers.find((center) => center.id === row.work_center_id)?.code || 'N/A'}</td><td className="mono">{percent(row.utilization)}</td><td className="mono">{minutes(row.busy_minutes)}</td><td className="mono">{minutes(row.available_minutes)}</td><td className="mono">{minutes(row.mean_queue_wait_minutes)}</td><td className="mono">{number(row.max_queue_length)}</td><td className="mono">{number(row.late_jobs_using_machine)}</td><td className="mono">{minutes(row.downtime_minutes)}</td><td className="mono">${number(row.operating_cost, 2)}</td></tr>)}</tbody></table></div></Panel>
      <div className="grid-2">
        <Panel title="Work-center throughput" meta="Completed operations per elapsed schedule hour"><div className="panel-body">{centerRows.length ? <><div className="data-table-wrap"><table className="data-table"><thead><tr><th>Work center</th><th>Operations</th><th>Operations / h</th></tr></thead><tbody>{centerRows.map((row) => <tr key={row.work_center_id}><td>{bootstrap.work_centers.find((center) => center.id === row.work_center_id)?.name || row.work_center_id}</td><td className="mono">{number(row.completed_operations)}</td><td className="mono">{number(row.operations_per_hour, 2)}</td></tr>)}</tbody></table></div><p className="tiny muted" style={{ margin: '12px 0 0' }}>This rate counts operations completed at each center. It is not finished-job throughput or a causal capacity estimate.</p></> : <Empty title="NO WORK-CENTER ACTIVITY" text="The selected schedule contains no assigned operations by work center." />}</div></Panel>
        <Panel title="Work in progress" meta={simulationId ? `Saved simulation ${simulationId.slice(0, 8).toUpperCase()}` : 'Requires a simulation'}><div className="panel-body">{simulationQuery.error && <ErrorNotice error={simulationQuery.error} retry={() => simulationQuery.refetch()} />}{wipPoints.length ? <><EngineeringChart option={wipChart} label={`Work in progress over simulated time; peak ${number(simulationMetrics?.max_wip)} jobs`} height={270} /><p className="tiny muted" style={{ margin: '10px 0 0' }}>Released, unfinished jobs in the saved simulation event stream. The line shows {replicationCount > 1 ? 'replication 1; aggregate simulation metrics summarize all replications' : 'the recorded simulation'}.</p></> : <Empty title="NO WIP SERIES" text={simulationId ? 'WIP events are not available for the selected simulation.' : 'Run a simulation for this schedule to inspect released, unfinished jobs over time.'} action={!simulationId ? <Link className="button small" href="/simulation">Run simulation<ArrowRight size={13} /></Link> : undefined} />}</div></Panel>
      </div>
      <div className="grid-2">
        <Panel title="Job completion delays" meta={`${lateJobs.length} late job${lateJobs.length === 1 ? '' : 's'} in this schedule`}><div className="panel-body">{lateJobs.length ? <><div className="data-table-wrap"><table className="data-table"><thead><tr><th>Job</th><th>Due / local</th><th>Completed / local</th><th>Tardiness</th></tr></thead><tbody>{lateJobs.slice().sort((a, b) => b.tardiness_minutes - a.tardiness_minutes).slice(0, 12).map((job) => { const origin = new Date(selectedSchedule.origin || '').getTime(); return <tr key={job.job_id}><td className="mono">{job.job_number}</td><td className="mono">{Number.isFinite(origin) ? dateTime(new Date(origin + job.due_min * 60_000).toISOString()) : `${number(job.due_min)} min from origin`}</td><td className="mono">{Number.isFinite(origin) ? dateTime(new Date(origin + job.completion_min * 60_000).toISOString()) : `${number(job.completion_min)} min from origin`}</td><td className="mono critical-text">{minutes(job.tardiness_minutes)}</td></tr>; })}</tbody></table></div>{lateJobs.length > 12 && <p className="tiny muted" style={{ margin: '10px 0 0' }}>Showing the 12 largest delays of {lateJobs.length} late jobs.</p>}</> : <Empty title="NO LATE JOBS" text="All jobs in this saved schedule finish by their configured due time." />}</div></Panel>
        <Panel title="Downtime exposure" meta="Machine unavailable minutes during the schedule observation"><div className="panel-body">{downtimeRows.length ? <><div className="data-table-wrap"><table className="data-table"><thead><tr><th>Machine</th><th>Downtime</th><th>Calendar closed</th><th>Available</th></tr></thead><tbody>{downtimeRows.map((row) => <tr key={row.machine_id}><td className="mono">{row.machine_code}</td><td className="mono">{minutes(row.downtime_minutes)}</td><td className="mono">{minutes(row.calendar_closed_minutes)}</td><td className="mono">{minutes(row.available_minutes)}</td></tr>)}</tbody></table></div><p className="tiny muted" style={{ margin: '12px 0 0' }}>These are observed unavailable intervals in the saved input. Their effect on completion requires a controlled scenario rerun.</p></> : <Empty title="NO MACHINE DOWNTIME" text="No machine downtime overlaps this schedule’s observation period. Calendar closures remain separate from downtime." />}</div></Panel>
      </div>
      <Panel title="Test capacity sensitivity" meta="Controlled operational experiment"><div className="panel-body"><p className="small muted">Choose a resource with sustained queue waiting, high utilization or repeated late jobs. Change capacity, processing time or shifts in Scenario Lab, recompute the schedule and compare measured outcomes with the saved baseline.</p><Link className="button" href="/scenarios">Open Scenario Lab<ArrowRight size={15} /></Link></div></Panel>
    </div>}
  </>;
}
