'use client';

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { RefreshCcw } from 'lucide-react';
import { api } from '@/lib/api';
import { dateTime, getDisplayTimeZone, isoToLocalInput, localInputToIso, minutes, number, percent } from '@/lib/format';
import { displayMachines } from '@/lib/snapshot';
import { useWorkspace } from '@/components/workspace-context';
import { EngineeringChart } from '@/components/analytics/engineering-chart';
import { Button, Empty, ErrorNotice, Loading, Metric, PageHeader, Panel, Status } from '@/components/ui';

type ResourceRow = {
  machine_id: string;
  machine_code: string;
  work_center_id?: string;
  busy_minutes: number;
  available_minutes: number;
  idle_minutes: number;
  unavailable_minutes: number;
  downtime_minutes: number;
  calendar_closed_minutes: number;
  overtime_minutes: number;
  utilization: number | null;
  operating_cost: number;
  scheduled_operation_count: number;
  due_workload_minutes: number;
  due_capacity_gap_minutes: number;
};
type CenterRow = {
  work_center_id: string;
  machine_count: number;
  busy_minutes: number;
  available_minutes: number;
  idle_minutes: number;
  downtime_minutes: number;
  utilization: number | null;
};
type TimeBucket = {
  start_min: number;
  end_min: number;
  start_time: string;
  end_time: string;
  items: { machine_id: string; busy_minutes: number; available_minutes: number; utilization: number | null }[];
};
type UtilizationResponse = {
  schedule_id: string;
  items: ResourceRow[];
  work_centers: CenterRow[];
  series: TimeBucket[];
  totals: Record<string, number | null>;
  interval_start: string;
  interval_end: string;
  bucket_minutes: number;
  definition: string;
  metric_reasons: Record<string, string>;
};

function sumRows(rows: ResourceRow[]) {
  const sum = (key: keyof ResourceRow) => rows.reduce((total, row) => total + (typeof row[key] === 'number' ? row[key] as number : 0), 0);
  const busy = sum('busy_minutes');
  const available = sum('available_minutes');
  return {
    busy, available,
    idle: sum('idle_minutes'),
    unavailable: sum('unavailable_minutes'),
    downtime: sum('downtime_minutes'),
    overtime: sum('overtime_minutes'),
    utilization: available ? busy / available : null,
  };
}

export function UtilizationPage() {
  const { bootstrap, schedules, selectedSchedule, selectedScheduleId, selectSchedule } = useWorkspace();
  const [centerId, setCenterId] = useState('all');
  const [machineSelection, setMachineSelection] = useState<{ scheduleId: string; machineId: string } | null>(null);
  const [windowSelection, setWindowSelection] = useState<{ scheduleId: string; from: string; to: string } | null>(null);
  const sourceMachines = useMemo(() => displayMachines(selectedSchedule, bootstrap.machines), [selectedSchedule, bootstrap.machines]);
  const origin = selectedSchedule?.origin || selectedSchedule?.snapshot?.origin;
  const fullEnd = Math.max(1, ...(selectedSchedule?.assignments || []).map((assignment) => assignment.end_min));
  const horizon = selectedSchedule?.snapshot?.horizon_minutes || selectedSchedule?.horizon_minutes || fullEnd;
  const defaultFrom = origin ? isoToLocalInput(origin) : '';
  const defaultTo = origin ? isoToLocalInput(new Date(new Date(origin).getTime() + fullEnd * 60_000).toISOString()) : '';
  const from = windowSelection?.scheduleId === selectedScheduleId ? windowSelection.from : defaultFrom;
  const to = windowSelection?.scheduleId === selectedScheduleId ? windowSelection.to : defaultTo;
  const machineId = machineSelection?.scheduleId === selectedScheduleId ? machineSelection.machineId : 'all';

  const range = useMemo(() => {
    if (!origin || !from || !to) return { start: 0, end: fullEnd, error: '' };
    try {
      const originMs = new Date(origin).getTime();
      const start = (new Date(localInputToIso(from)).getTime() - originMs) / 60_000;
      const end = (new Date(localInputToIso(to)).getTime() - originMs) / 60_000;
      if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end > horizon || end <= start) {
        return { start, end, error: 'Choose a positive interval within the selected schedule horizon.' };
      }
      return { start, end, error: '' };
    } catch (error) {
      return { start: 0, end: fullEnd, error: error instanceof Error ? error.message : 'Enter a valid date range.' };
    }
  }, [origin, from, to, fullEnd, horizon]);

  const query = useQuery({
    queryKey: ['utilization', selectedScheduleId, range.start, range.end],
    queryFn: () => api.utilization(selectedScheduleId!, range.start, range.end) as Promise<UtilizationResponse>,
    enabled: !!selectedScheduleId && selectedSchedule?.status === 'completed' && !range.error,
  });
  const allRows = useMemo(() => query.data?.items || [], [query.data?.items]);
  const validCenterId = bootstrap.work_centers.some((center) => center.id === centerId) ? centerId : 'all';
  const rows = useMemo(() => allRows.filter((row) =>
    (validCenterId === 'all' || row.work_center_id === validCenterId)
    && (machineId === 'all' || row.machine_id === machineId),
  ), [allRows, validCenterId, machineId]);
  const totals = useMemo(() => sumRows(rows), [rows]);
  const centerRows = (query.data?.work_centers || []).filter((row) => validCenterId === 'all' || row.work_center_id === validCenterId);
  const trendMachine = machineId === 'all'
    ? rows.slice().sort((a, b) => (b.utilization ?? -1) - (a.utilization ?? -1))[0]
    : rows[0];

  const compositionOption = useMemo(() => ({
    legend: { bottom: 2, data: ['Busy', 'Idle available', 'Unavailable'], textStyle: { color: 'var(--text-secondary)' } },
    grid: { left: 100, right: 72, top: 22, bottom: 60 },
    xAxis: { type: 'value' as const, name: 'Minutes', axisLabel: { formatter: '{value}' } },
    yAxis: { type: 'category' as const, data: rows.slice().reverse().map((row) => row.machine_code) },
    tooltip: { trigger: 'axis' as const, axisPointer: { type: 'shadow' as const } },
    series: [
      { name: 'Busy', type: 'bar' as const, stack: 'time', data: rows.slice().reverse().map((row) => row.busy_minutes), itemStyle: { color: 'var(--action)' } },
      { name: 'Idle available', type: 'bar' as const, stack: 'time', data: rows.slice().reverse().map((row) => row.idle_minutes), itemStyle: { color: 'var(--graphite)' } },
      { name: 'Unavailable', type: 'bar' as const, stack: 'time', data: rows.slice().reverse().map((row) => row.unavailable_minutes), itemStyle: { color: 'var(--amber)' } },
    ],
  }), [rows]);

  const dueCapacityOption = useMemo(() => ({
    legend: { bottom: 2, data: ['Available capacity', 'Due workload'], textStyle: { color: 'var(--text-secondary)' } },
    grid: { left: 100, right: 72, top: 22, bottom: 60 },
    xAxis: { type: 'value' as const, name: 'Minutes' },
    yAxis: { type: 'category' as const, data: rows.slice().reverse().map((row) => row.machine_code) },
    tooltip: { trigger: 'axis' as const, axisPointer: { type: 'shadow' as const } },
    series: [
      { name: 'Available capacity', type: 'bar' as const, data: rows.slice().reverse().map((row) => row.available_minutes), itemStyle: { color: 'var(--action)' } },
      { name: 'Due workload', type: 'bar' as const, itemStyle: { color: 'var(--graphite)' }, data: rows.slice().reverse().map((row) => ({ value: row.due_workload_minutes, itemStyle: { color: row.due_capacity_gap_minutes > 0 ? 'var(--orange)' : 'var(--graphite)' } })) },
    ],
  }), [rows]);

  const trendOption = useMemo(() => ({
    grid: { left: 58, right: 24, top: 24, bottom: 70 },
    xAxis: {
      type: 'category' as const,
      name: `Time / ${getDisplayTimeZone()}`,
      data: (query.data?.series || []).map((bucket) => dateTime(bucket.start_time, { month: 'numeric', day: 'numeric', hour: '2-digit' })),
      axisLabel: { rotate: 35 },
    },
    yAxis: { type: 'value' as const, min: 0, max: 100, name: 'Utilization (%)' },
    tooltip: { trigger: 'axis' as const, valueFormatter: (value: unknown) => typeof value === 'number' ? `${value.toFixed(1)}%` : 'N/A' },
    series: [{
      name: trendMachine?.machine_code || 'Machine',
      type: 'line' as const,
      connectNulls: false,
      showSymbol: true,
      symbolSize: 6,
      lineStyle: { width: 2 },
      itemStyle: { color: 'var(--action)' },
      data: (query.data?.series || []).map((bucket) => {
        const value = bucket.items.find((item) => item.machine_id === trendMachine?.machine_id)?.utilization;
        return typeof value === 'number' ? Math.round(value * 1000) / 10 : null;
      }),
    }],
  }), [query.data?.series, trendMachine?.machine_id, trendMachine?.machine_code]);

  return <>
    <PageHeader index="08" title="Resource Utilization" subtitle="Inspect scheduled busy, idle and unavailable time for each machine.">
      <Button onClick={() => query.refetch()} disabled={!selectedScheduleId || selectedSchedule?.status !== 'completed' || !!range.error}><RefreshCcw />Refresh capacity</Button>
    </PageHeader>

    <div className="toolbar">
      <span className="toolbar-label">Schedule</span>
      <select className="control" style={{ width: 250 }} value={selectedScheduleId || ''} onChange={(event) => selectSchedule(event.target.value || null)} aria-label="Schedule run">
        <option value="">Choose completed schedule</option>
        {schedules.filter((schedule) => schedule.status === 'completed').map((schedule) => <option key={schedule.id} value={schedule.id}>{schedule.algorithm.toUpperCase()} · {dateTime(schedule.created_at)}</option>)}
      </select>
      <span className="toolbar-label">Work center</span>
      <select className="control" style={{ width: 170 }} value={validCenterId} onChange={(event) => { setCenterId(event.target.value); setMachineSelection({ scheduleId: selectedScheduleId || '', machineId: 'all' }); }} aria-label="Work center"><option value="all">All work centers</option>{bootstrap.work_centers.map((center) => <option value={center.id} key={center.id}>{center.code} / {center.name}</option>)}</select>
      <span className="toolbar-label">Machine</span>
      <select className="control" style={{ width: 145 }} value={machineId} onChange={(event) => setMachineSelection({ scheduleId: selectedScheduleId || '', machineId: event.target.value })} aria-label="Machine"><option value="all">All machines</option>{sourceMachines.filter((machine) => validCenterId === 'all' || machine.work_center_id === validCenterId).map((machine) => <option key={machine.id} value={machine.id}>{machine.machine_code}</option>)}</select>
      <span className="toolbar-spacer" />{selectedSchedule && <Status value={selectedSchedule.solver_status || selectedSchedule.status} />}
    </div>

    {selectedSchedule && <div className="toolbar">
      <label className="field"><span className="field-label">From / {getDisplayTimeZone()}</span><input className="control" type="datetime-local" value={from} onChange={(event) => setWindowSelection({ scheduleId: selectedSchedule.id, from: event.target.value, to })} /></label>
      <label className="field"><span className="field-label">To / {getDisplayTimeZone()}</span><input className="control" type="datetime-local" value={to} onChange={(event) => setWindowSelection({ scheduleId: selectedSchedule.id, from, to: event.target.value })} /></label>
      <span className="tiny muted">The selected interval filters capacity calculations and chart buckets. Timestamps use the facility time zone.</span>
    </div>}
    {range.error && <div className="field-error" role="alert" style={{ marginBottom: 12 }}>{range.error}</div>}
    {query.error && <ErrorNotice error={query.error} retry={() => query.refetch()} />}

    <div className="metric-grid">
      <Metric label="Busy production" value={rows.length ? minutes(totals.busy) : 'N/A'} context="Assigned processing" />
      <Metric label="Available capacity" value={rows.length ? minutes(totals.available) : 'N/A'} context="Calendar minus downtime" />
      <Metric label="Idle available" value={rows.length ? minutes(totals.idle) : 'N/A'} context="Open minus processing" />
      <Metric label="Unavailable time" value={rows.length ? minutes(totals.unavailable) : 'N/A'} context="Closed / downtime" />
      <Metric label="Downtime" value={rows.length ? minutes(totals.downtime) : 'N/A'} context="Within planned shifts" />
      <Metric label="Utilization" value={rows.length ? percent(totals.utilization) : 'N/A'} context="Busy / available" />
    </div>

    <div className="stack">
      <Panel title="Capacity composition" meta="Available versus assigned processing minutes by machine"><div className="panel-body">
        {query.isLoading ? <Loading label="Calculating capacity" /> : rows.length ? <EngineeringChart option={compositionOption} label={`Available and assigned processing minutes across ${rows.length} machines`} height={Math.max(300, rows.length * 45 + 90)} /> : <Empty title="NO CAPACITY DATA" text="Select a completed schedule and valid time interval." />}
      </div></Panel>

      <Panel title="Available versus due workload" meta="Remaining assigned work for jobs due by the selected interval end"><div className="panel-body">
        {rows.length ? <EngineeringChart option={dueCapacityOption} label={`Available capacity and work due by interval end across ${rows.length} machines, in minutes`} height={Math.max(300, rows.length * 45 + 90)} /> : <Empty title="NO DUE-WORKLOAD DATA" text="Select a completed schedule and time interval." />}
        {rows.length > 0 && <div className="surface-note" style={{ marginTop: 12 }}>Due workload counts processing still unfinished at the interval start for jobs due by its end, using each operation’s saved machine assignment. Orange bars exceed available capacity. This is a capacity screen; deadline feasibility still depends on job order and exact release times.</div>}
      </div></Panel>

      <Panel title="Resource utilization heatmap" meta={`Scheduled machine utilization / ${query.data?.bucket_minutes || '—'} minute buckets`}>
        <div className="data-table-wrap"><table className="data-table" style={{ width: 'max-content', minWidth: '100%' }} aria-label="Machine utilization by time bucket"><thead><tr><th scope="col">Machine</th>{(query.data?.series || []).map((bucket) => <th scope="col" className="mono" key={bucket.start_min} title={`${dateTime(bucket.start_time)} to ${dateTime(bucket.end_time)}`}>{dateTime(bucket.start_time, { month: 'numeric', day: 'numeric', hour: '2-digit' })}</th>)}</tr></thead><tbody>{rows.map((row) => <tr key={row.machine_id}><th scope="row" className="mono">{row.machine_code}</th>{(query.data?.series || []).map((bucket) => {
          const cell = bucket.items.find((item) => item.machine_id === row.machine_id);
          const value = cell?.utilization;
          const background = typeof value === 'number'
            ? `color-mix(in srgb, var(${value >= .85 ? '--orange' : '--action'}) ${Math.round(12 + Math.min(1, value) * 40)}%, var(--surface))`
            : 'var(--surface-2)';
          return <td key={bucket.start_min} className="mono" style={{ minWidth: 72, background }} title={`${row.machine_code}: ${number(cell?.busy_minutes)} busy / ${number(cell?.available_minutes)} available minutes`} aria-label={`${row.machine_code}, ${dateTime(bucket.start_time)}: ${typeof value === 'number' ? percent(value) : 'no available time'}`}>{typeof value === 'number' ? percent(value, 0) : '—'}</td>;
        })}</tr>)}</tbody></table></div>
        {!rows.length && <Empty title="NO HEATMAP DATA" text="Complete a schedule to inspect time-bucket utilization." />}
      </Panel>

      <Panel title="Machine-level trend" meta={trendMachine ? `${trendMachine.machine_code} · highest utilization among visible machines; select another machine above` : 'Select a machine'}><div className="panel-body">
        {trendMachine && query.data?.series.length ? <EngineeringChart option={trendOption} label={`Scheduled utilization trend for ${trendMachine.machine_code} in percent`} height={320} /> : <Empty title="NO TREND DATA" text="Select a completed schedule and machine." />}
      </div></Panel>

      <div className="grid-2">
        <Panel title="Work-center comparisons" meta="All machines in each center / selected time interval"><div className="data-table-wrap"><table className="data-table"><thead><tr><th>Work center</th><th>Machines</th><th>Busy</th><th>Available</th><th>Utilization</th></tr></thead><tbody>{centerRows.map((row) => <tr key={row.work_center_id}><td>{bootstrap.work_centers.find((center) => center.id === row.work_center_id)?.name || row.work_center_id || 'Unassigned'}</td><td className="mono">{number(row.machine_count)}</td><td className="mono">{minutes(row.busy_minutes)}</td><td className="mono">{minutes(row.available_minutes)}</td><td className="mono">{percent(row.utilization)}</td></tr>)}</tbody></table></div>{!centerRows.length && <Empty title="NO WORK-CENTER DATA" text="Select a completed schedule." />}</Panel>

        <Panel title="Time accounting" meta="Selected machines / selected interval"><div className="panel-body"><dl className="detail-list"><dt>Busy processing</dt><dd>{rows.length ? minutes(totals.busy) : 'N/A'}</dd><dt>Idle but available</dt><dd>{rows.length ? minutes(totals.idle) : 'N/A'}</dd><dt>Planned downtime</dt><dd>{rows.length ? minutes(totals.downtime) : 'N/A'}</dd><dt>Calendar closed</dt><dd>{rows.length ? minutes(totals.unavailable - totals.downtime) : 'N/A'}</dd><dt>Overtime</dt><dd>{rows.length ? minutes(totals.overtime) : 'N/A'}</dd></dl><div className="surface-note" style={{ marginTop: 16 }}>Overtime is zero for a valid schedule because the nonpreemptive scheduler cannot assign work outside machine availability. Closed shifts and downtime are excluded from utilization denominators.</div></div></Panel>
      </div>

      <Panel title="Machine utilization detail" meta={query.data?.definition || 'Busy production minutes divided by available operating minutes'}><div className="data-table-wrap"><table className="data-table"><thead><tr><th>Machine</th><th>Work center</th><th>Utilization</th><th>Busy</th><th>Idle available</th><th>Available</th><th>Due workload</th><th>Capacity gap</th><th>Calendar closed</th><th>Downtime</th><th>Overtime</th><th>Operations</th></tr></thead><tbody>{rows.map((row) => <tr key={row.machine_id}><td className="mono"><strong>{row.machine_code}</strong></td><td>{bootstrap.work_centers.find((center) => center.id === row.work_center_id)?.code || 'N/A'}</td><td className="mono" title={row.utilization === null ? 'No available production time in this interval' : 'Busy / available production time'}>{percent(row.utilization)}</td><td className="mono">{minutes(row.busy_minutes)}</td><td className="mono">{minutes(row.idle_minutes)}</td><td className="mono">{minutes(row.available_minutes)}</td><td className="mono">{minutes(row.due_workload_minutes)}</td><td className={`mono ${row.due_capacity_gap_minutes > 0 ? 'critical-text' : ''}`}>{minutes(row.due_capacity_gap_minutes)}</td><td className="mono">{minutes(row.calendar_closed_minutes)}</td><td className="mono">{minutes(row.downtime_minutes)}</td><td className="mono">{minutes(row.overtime_minutes)}</td><td className="mono">{number(row.scheduled_operation_count)}</td></tr>)}</tbody></table></div>{!rows.length && <Empty title="NO MACHINE METRICS" text="Complete a schedule to populate resource utilization." />}</Panel>
      <div className="surface-note">Utilization = busy production time ÷ available operating time in the selected interval. Closed calendar periods and planned downtime are excluded. The heatmap and trend use saved schedule assignments, not live machine telemetry. {query.data?.metric_reasons?.utilization || ''}</div>
    </div>
  </>;
}
