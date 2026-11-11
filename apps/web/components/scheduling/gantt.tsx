'use client';

import { useMemo, useState } from 'react';
import type { Machine, ProductionJob, ScheduleRun } from '@/lib/types';
import { dateTime, getDisplayTimeZone, minutes } from '@/lib/format';
import { displayJobs, displayMachines } from '@/lib/snapshot';
import { Empty } from '@/components/ui';

const jobColors = ['var(--action)', 'var(--graphite)', 'var(--success)', 'var(--orange)'];
const emptyAssignments: NonNullable<ScheduleRun['assignments']> = [];

function hash(value: string) {
  let result = 0;
  for (const char of value) result = (result * 31 + char.charCodeAt(0)) | 0;
  return Math.abs(result);
}

function clip([start, end]: number[], lower: number, upper: number): [number, number] | null {
  const first = Math.max(lower, start);
  const last = Math.min(upper, end);
  return last > first ? [first, last] : null;
}

function complement(windows: number[][], end: number): [number, number][] {
  const result: [number, number][] = [];
  let cursor = 0;
  for (const window of windows.slice().sort((a, b) => a[0] - b[0])) {
    const span = clip(window, 0, end);
    if (!span) continue;
    if (span[0] > cursor) result.push([cursor, span[0]]);
    cursor = Math.max(cursor, span[1]);
  }
  if (cursor < end) result.push([cursor, end]);
  return result;
}

export function GanttChart({
  schedule, machines, jobs, compact = false, machineFilter,
  zoom: externalZoom, rangeStartMin, rangeEndMin,
}: {
  schedule: ScheduleRun | null;
  machines: Machine[];
  jobs: ProductionJob[];
  compact?: boolean;
  machineFilter?: string;
  zoom?: number;
  onZoomChange?: (zoom: number) => void;
  rangeStartMin?: number;
  rangeEndMin?: number;
}) {
  const [selectedOperationId, setSelectedOperationId] = useState<string | null>(null);
  const zoom = externalZoom ?? 1;
  const assignments = schedule?.assignments || emptyAssignments;
  const snapshot = schedule?.snapshot;
  const sourceMachines = useMemo(() => displayMachines(schedule, machines), [schedule, machines]);
  const sourceJobs = useMemo(() => displayJobs(schedule, jobs), [schedule, jobs]);
  const visibleMachines = sourceMachines.filter((machine) => !machineFilter || machineFilter === 'all' || machine.id === machineFilter);

  const bounds = useMemo(() => {
    const lastAssignment = Math.max(60, ...assignments.map((assignment) => Number(assignment.end_min) || 0));
    const fullEnd = Math.max(1, Math.min(snapshot?.horizon_minutes || lastAssignment, Math.ceil(lastAssignment / 60) * 60));
    const requestedStart = rangeStartMin !== undefined && Number.isFinite(rangeStartMin) ? Math.floor(rangeStartMin) : 0;
    const requestedEnd = rangeEndMin !== undefined && Number.isFinite(rangeEndMin) ? Math.ceil(rangeEndMin) : fullEnd;
    const start = Math.max(0, Math.min(fullEnd - 1, requestedStart));
    const end = Math.max(start + 1, Math.min(fullEnd, requestedEnd));
    return { start, end, span: end - start };
  }, [assignments, snapshot?.horizon_minutes, rangeStartMin, rangeEndMin]);

  const visibleAssignments = assignments.filter((assignment) => assignment.end_min > bounds.start && assignment.start_min < bounds.end);
  const selected = visibleAssignments.find((assignment) => assignment.operation_id === selectedOperationId) || null;
  const selectedJob = selected ? sourceJobs.find((job) => job.id === selected.job_id) : undefined;
  const selectedOperation = selectedJob?.operations.find((operation) => operation.id === selected?.operation_id);
  const selectedMachine = sourceMachines.find((machine) => machine.id === selected?.machine_id);
  const orderedOperations = selectedJob?.operations.slice().sort((a, b) => a.operation_number - b.operation_number) || [];
  const selectedPosition = orderedOperations.findIndex((operation) => operation.id === selectedOperationId);
  const predecessor = selectedPosition > 0 ? orderedOperations[selectedPosition - 1] : undefined;
  const successor = selectedPosition >= 0 ? orderedOperations[selectedPosition + 1] : undefined;

  const completionByJob = useMemo(() => {
    const result = new Map<string, number>();
    for (const assignment of assignments) {
      const end = new Date(assignment.end_time).getTime();
      if (Number.isFinite(end)) result.set(assignment.job_id, Math.max(result.get(assignment.job_id) || 0, end));
    }
    return result;
  }, [assignments]);
  const lateJobIds = useMemo(() => new Set(sourceJobs.filter((job) => {
    const completion = completionByJob.get(job.id);
    return completion !== undefined && completion > new Date(job.due_time).getTime();
  }).map((job) => job.id)), [sourceJobs, completionByJob]);
  const selectedCompletion = selectedJob ? completionByJob.get(selectedJob.id) : undefined;
  const dueDifference = selectedJob && selectedCompletion !== undefined
    ? Math.round((new Date(selectedJob.due_time).getTime() - selectedCompletion) / 60_000)
    : undefined;
  const dueRelationship = dueDifference === undefined ? 'N/A' : dueDifference < 0
    ? `Late by ${minutes(-dueDifference)}` : dueDifference > 0
      ? `On time by ${minutes(dueDifference)}` : 'On time at the due date';

  const width = Math.max(720, Math.ceil(bounds.span / 60 * 56 * zoom));
  const tickHours = Math.max(1, Math.ceil(bounds.span / 60 / 13 / zoom));
  const tickStep = tickHours * 60;
  const ticks = [bounds.start];
  for (let minute = Math.ceil((bounds.start + 1) / tickStep) * tickStep; minute < bounds.end; minute += tickStep) ticks.push(minute);
  const originTime = schedule?.origin || schedule?.snapshot?.origin;
  const origin = originTime ? new Date(originTime).getTime() : null;
  const left = (minute: number) => `${(minute - bounds.start) / bounds.span * 100}%`;
  const spanWidth = (start: number, end: number) => `${(end - start) / bounds.span * 100}%`;

  if (!schedule || !assignments.length) return <Empty title="NO SCHEDULE DATA" text="Generate a feasible schedule to inspect machine assignments and operation timing." />;

  return <div className={`gantt-grid ${compact ? 'compact' : ''}`}>
    <div className="gantt-main"><div className="gantt-shell" style={compact ? { maxHeight: 315 } : undefined} role="region" aria-label={`Gantt chart with ${visibleAssignments.filter((assignment) => visibleMachines.some((machine) => machine.id === assignment.machine_id)).length} visible operations across ${visibleMachines.length} machines, times in ${getDisplayTimeZone()}`} tabIndex={0}>
      <div className="gantt-inner" style={{ width: width + 162 }}>
        <div className="gantt-axis"><div className="gantt-machine-label">MACHINE / {getDisplayTimeZone()}</div><div className="gantt-track" style={{ width }}>
          {ticks.map((minute) => <div className="gantt-tick" key={minute} style={{ left: left(minute) }}>{origin ? dateTime(new Date(origin + minute * 60_000).toISOString(), { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : `${minute / 60}h`}</div>)}
        </div></div>
        {visibleMachines.map((machine) => {
          const resource = snapshot?.machines.find((item) => item.id === machine.id);
          const availability = resource?.availability || [[0, bounds.end]];
          const downtime = resource?.downtime || (origin ? machine.downtime.map((item) => [(new Date(item.start_time).getTime() - origin) / 60_000, (new Date(item.end_time).getTime() - origin) / 60_000]) : []);
          const machineAssignments = visibleAssignments.filter((assignment) => assignment.machine_id === machine.id);
          return <div className="gantt-row" key={machine.id}>
            <div className="gantt-machine-label"><strong>{machine.machine_code}</strong><span className="sub">{machineAssignments.length ? `${machineAssignments.length} visible operations` : 'No assignment in range'}</span></div>
            <div className="gantt-track" style={{ width, backgroundSize: `${100 * tickStep / bounds.span}% 100%` }}>
              {complement(availability, bounds.end).map((window, index) => {
                const span = clip(window, bounds.start, bounds.end);
                return span && <div className="calendar-closed" key={`closed-${index}`} style={{ left: left(span[0]), width: spanWidth(span[0], span[1]) }} title="Calendar closed / no production shift" />;
              })}
              {availability.flatMap(([start, end], index) => [start, end].filter((minute) => minute > bounds.start && minute < bounds.end).map((minute, side) => <div className="shift-boundary" key={`shift-${index}-${side}`} style={{ left: left(minute) }} title="Shift boundary" />))}
              {downtime.map((window, index) => {
                const span = clip(window, bounds.start, bounds.end);
                return span && <div className="downtime" key={`down-${index}`} title="Machine unavailable / planned or scenario downtime" style={{ left: left(span[0]), width: spanWidth(span[0], span[1]) }} />;
              })}
              {machineAssignments.map((assignment) => {
                const job = sourceJobs.find((item) => item.id === assignment.job_id);
                const operation = job?.operations.find((item) => item.id === assignment.operation_id);
                const span = clip([assignment.start_min, assignment.end_min], bounds.start, bounds.end);
                if (!span) return null;
                const isSelected = selectedOperationId === assignment.operation_id;
                const isPredecessor = predecessor?.id === assignment.operation_id;
                const isSuccessor = successor?.id === assignment.operation_id;
                const isLate = lateJobIds.has(assignment.job_id);
                const muted = !!selected && !isSelected && !isPredecessor && !isSuccessor;
                const className = ['gantt-operation', isSelected && 'selected', isPredecessor && 'dependency-predecessor', isSuccessor && 'dependency-successor', isLate && 'late', muted && 'dependency-muted'].filter(Boolean).join(' ');
                return <button className={className} key={assignment.operation_id} style={{ left: left(span[0]), width: spanWidth(span[0], span[1]), color: jobColors[hash(assignment.job_id) % jobColors.length] }} onClick={() => setSelectedOperationId(assignment.operation_id)} aria-pressed={isSelected} title={`${job?.job_number || assignment.job_id} · ${operation?.operation_name || 'Operation'}${isLate ? ' · LATE JOB' : ''} · ${dateTime(assignment.start_time)}–${dateTime(assignment.end_time)}`} aria-label={`${job?.job_number || 'Job'} ${operation?.operation_name || 'operation'} on ${machine.machine_code} from ${dateTime(assignment.start_time)} to ${dateTime(assignment.end_time)}${isLate ? ', job is late' : ''}`}><span>{job?.job_number || assignment.job_id.slice(0, 6)}{isLate && <b className="gantt-late-text"> LATE</b>}</span><small>{operation?.operation_name || 'Operation'}</small></button>;
              })}
            </div>
          </div>;
        })}
      </div>
    </div><div className="gantt-legend" aria-label="Gantt legend"><span><i className="legend-operation" /> Scheduled operation</span><span><i className="legend-closed" /> Closed shift</span><span><i className="legend-downtime" /> Downtime</span><span><i className="legend-late" /> Late job</span>{selected && <><span><i className="legend-predecessor" /> Predecessor</span><span><i className="legend-successor" /> Successor</span></>}</div></div>
    {!compact && <aside className="gantt-inspector"><div className="section-kicker">OPERATION INSPECTOR / {getDisplayTimeZone()}</div>{selected && selectedJob ? <>
      <h3 style={{ margin: '8px 0 16px' }}>{selectedJob.job_number} / OP {selectedOperation?.operation_number ?? '—'}</h3>
      <dl className="detail-list">
        <dt>Operation</dt><dd>{selectedOperation?.operation_name || 'N/A'}</dd>
        <dt>Operation ID</dt><dd className="mono">{selected.operation_id.slice(0, 8).toUpperCase()}</dd>
        <dt>Job ID</dt><dd className="mono">{selectedJob.job_number}</dd>
        <dt>Machine</dt><dd>{selectedMachine?.machine_code || selected.machine_id}</dd>
        <dt>Start</dt><dd>{dateTime(selected.start_time)}</dd>
        <dt>Finish</dt><dd>{dateTime(selected.end_time)}</dd>
        <dt>Duration</dt><dd>{minutes(selected.end_min - selected.start_min)}</dd>
        <dt>Predecessor</dt><dd>{predecessor ? `OP ${predecessor.operation_number} / ${predecessor.operation_name}` : 'First operation'}</dd>
        <dt>Successor</dt><dd>{successor ? `OP ${successor.operation_number} / ${successor.operation_name}` : 'Final operation'}</dd>
        <dt>Job completion</dt><dd>{selectedCompletion !== undefined ? dateTime(new Date(selectedCompletion).toISOString()) : 'N/A'}</dd>
        <dt>Due date</dt><dd>{dateTime(selectedJob.due_time)}</dd>
        <dt>Due relationship</dt><dd className={dueDifference !== undefined && dueDifference < 0 ? 'critical-text' : 'success-text'}>{dueRelationship}</dd>
      </dl>
      <div className="surface-note" style={{ marginTop: 18 }}>Assignment from the saved {schedule.algorithm.toUpperCase()} run. Highlighted bars show adjacent job operations. Manual dragging is disabled to preserve schedule feasibility.</div>
    </> : <p className="muted small" style={{ marginTop: 10 }}>Select an operation to inspect exact timing, dependencies and due-date context.</p>}</aside>}
  </div>;
}
