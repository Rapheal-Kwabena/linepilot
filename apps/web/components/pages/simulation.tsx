'use client';

import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Pause, Play, RotateCcw, SkipForward, Square } from 'lucide-react';
import { api } from '@/lib/api';
import { dateTime, number } from '@/lib/format';
import type { SimulationEvent } from '@/lib/types';
import { isFinished } from '@/lib/types';
import { displayJobs, displayMachines } from '@/lib/snapshot';
import { useWorkspace } from '@/components/workspace-context';
import { useUi } from '@/stores/ui';
import { Button, Empty, ErrorNotice, Metric, PageHeader, Panel, Status } from '@/components/ui';
import { EngineeringChart } from '@/components/analytics/engineering-chart';
import { FactoryCanvas } from '@/components/simulation/factory-canvas';

type WipPoint = { time_min: number; wip: number };
type Interval = { lower: number; upper: number; method?: string };

const intervalMetrics = [
  { key: 'completed_jobs', label: 'Completed jobs', unit: 'jobs' },
  { key: 'throughput_jobs_per_hour', label: 'Throughput', unit: 'jobs/h' },
  { key: 'on_time_delivery_rate', label: 'On-time delivery', unit: '%' },
  { key: 'mean_job_flow_minutes', label: 'Mean job flow', unit: 'min' },
  { key: 'mean_job_waiting_minutes', label: 'Mean job waiting', unit: 'min' },
  { key: 'average_machine_utilization', label: 'Machine utilization', unit: '%' },
  { key: 'operating_cost', label: 'Machine operating cost', unit: '$' },
] as const;

function intervalValue(value: number, unit: string) {
  if (unit === '%') return `${number(value * 100, 1)}%`;
  if (unit === '$') return `$${number(value, 2)}`;
  return `${number(value, 2)} ${unit}`;
}

export function SimulationPage() {
  const { bootstrap, schedules, selectedSchedule, selectedScheduleId, selectSchedule } = useWorkspace();
  const { selectedSimulationId, setSelectedSimulationId } = useUi();
  const [mode, setMode] = useState<'playback' | 'stochastic'>('playback');
  const [seed, setSeed] = useState(42);
  const [replications, setReplications] = useState(1);
  const [variability, setVariability] = useState(.1);
  const [distribution, setDistribution] = useState<'fixed' | 'triangular' | 'lognormal'>('triangular');
  const [failureRate, setFailureRate] = useState(0);
  const [meanRepair, setMeanRepair] = useState(60);
  const [horizonHours, setHorizonHours] = useState(168);
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [machineFilter, setMachineFilter] = useState('all');
  const [eventFilter, setEventFilter] = useState('all');
  const [actionError, setActionError] = useState<unknown>(null);
  const savedQuery = useQuery({ queryKey: ['simulations', selectedScheduleId], queryFn: () => api.simulations(selectedScheduleId!), enabled: !!selectedScheduleId, refetchInterval: (query) => query.state.data?.items.some((item) => !isFinished(item.status)) ? 1500 : false });
  useEffect(() => { if (!selectedSimulationId && savedQuery.data?.items.length) setSelectedSimulationId(savedQuery.data.items[0].id); }, [selectedSimulationId, savedQuery.data, setSelectedSimulationId]);
  const simQuery = useQuery({ queryKey: ['simulation', selectedSimulationId], queryFn: () => api.simulation(selectedSimulationId!), enabled: !!selectedSimulationId, refetchInterval: (query) => query.state.data && !isFinished(query.state.data.status) ? 1300 : false });
  const eventsQuery = useQuery({ queryKey: ['simulation-events', selectedSimulationId], queryFn: () => api.simulationEvents(selectedSimulationId!), enabled: !!selectedSimulationId && simQuery.data?.status === 'completed' && !simQuery.data.events?.length });
  const simulation = simQuery.data;
  const sourceScheduleQuery = useQuery({ queryKey: ['schedule', simulation?.schedule_run_id], queryFn: () => api.schedule(simulation!.schedule_run_id), enabled: !!simulation?.schedule_run_id && simulation.schedule_run_id !== selectedScheduleId });
  const playbackSchedule = simulation?.schedule_run_id === selectedScheduleId ? selectedSchedule : sourceScheduleQuery.data;
  const playbackMachines = useMemo(() => displayMachines(playbackSchedule || null, bootstrap.machines), [playbackSchedule, bootstrap.machines]);
  const playbackJobs = useMemo(() => displayJobs(playbackSchedule || null, bootstrap.jobs), [playbackSchedule, bootstrap.jobs]);
  const events = useMemo(() => simulation?.events?.length ? simulation.events : eventsQuery.data?.items || [], [simulation?.events, eventsQuery.data]);
  const metrics = simulation?.metrics || simulation?.summary_metrics || {};
  const observedReplications = Number(simulation?.configuration?.replications || 1);
  const metricContext = observedReplications > 1 ? `Mean of ${observedReplications} replications` : 'Observed simulation';
  const rawWip = metrics.wip_time_series;
  const wipPoints = useMemo<WipPoint[]>(() => Array.isArray(rawWip) ? rawWip.filter((point): point is WipPoint => point && typeof point.time_min === 'number' && typeof point.wip === 'number') : [], [rawWip]);
  const firstReplicationWipPeak = Math.max(0, ...wipPoints.map((point) => point.wip));
  const rawIntervals = metrics.confidence_intervals;
  const confidenceIntervals = useMemo(() => rawIntervals && typeof rawIntervals === 'object' && !Array.isArray(rawIntervals) ? rawIntervals as Record<string, Interval> : {}, [rawIntervals]);
  const intervalRows = intervalMetrics.flatMap(({ key, label, unit }) => {
    const mean = metrics[key];
    const interval = confidenceIntervals[key];
    return typeof mean === 'number' && Number.isFinite(mean) && interval && Number.isFinite(interval.lower) && Number.isFinite(interval.upper)
      ? [{ key, label, unit, mean, lower: interval.lower, upper: interval.upper }]
      : [];
  });
  const wipOption = useMemo(() => ({ grid: { left: 55, right: 25, top: 30, bottom: 58 }, xAxis: { type: 'value' as const, name: 'Minute from origin', min: 0 }, yAxis: { type: 'value' as const, name: 'Jobs in process', min: 0, minInterval: 1 }, tooltip: { trigger: 'axis' as const }, series: [{ name: 'Work in progress', type: 'line' as const, step: 'end' as const, showSymbol: false, data: wipPoints.map((point) => [point.time_min, point.wip]), lineStyle: { color: 'var(--action)', width: 2 }, itemStyle: { color: 'var(--action)' } }] }), [wipPoints]);
  useEffect(() => { if (selectedSchedule?.horizon_minutes) setHorizonHours(selectedSchedule.horizon_minutes / 60); }, [selectedSchedule?.id, selectedSchedule?.horizon_minutes]);
  const create = useMutation({ mutationFn: () => api.createSimulation({ schedule_run_id: selectedScheduleId, mode, random_seed: seed, replications: mode === 'stochastic' ? replications : 1, processing_time_distribution: mode === 'stochastic' ? distribution : 'fixed', processing_time_variability: mode === 'stochastic' ? variability : 0, failure_rate_per_hour: mode === 'stochastic' ? failureRate : 0, mean_repair_minutes: meanRepair, ...(mode === 'stochastic' ? { horizon_minutes: Math.round(horizonHours * 60) } : {}) }), onSuccess: (run) => { setSelectedSimulationId(run.id); setIndex(0); setPlaying(false); savedQuery.refetch(); }, onError: setActionError });
  const cancel = useMutation({ mutationFn: () => api.cancelSimulation(selectedSimulationId!), onSuccess: () => simQuery.refetch(), onError: setActionError });
  useEffect(() => { if (!playing || !events.length) return; const timer = window.setInterval(() => setIndex((current) => { if (current >= events.length - 1) { setPlaying(false); return current; } return current + 1; }), Math.max(50, 650 / speed)); return () => window.clearInterval(timer); }, [playing, speed, events.length]);
  const eventMinute = (event: SimulationEvent) => Number(event.time_min ?? event.simulation_time ?? 0);
  const timeLabel = (event: SimulationEvent) => event.time ? dateTime(event.time, { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short' }) : playbackSchedule?.origin ? dateTime(new Date(new Date(playbackSchedule.origin).getTime() + eventMinute(event) * 60_000).toISOString(), { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short' }) : `${number(eventMinute(event), 1)}m`;
  const filteredEvents = events.slice(0, index + 1).filter((e) => (machineFilter === 'all' || e.machine_id === machineFilter) && (eventFilter === 'all' || e.event_type === eventFilter)).slice(-100).reverse();
  const metricValue = (key: string) => typeof metrics[key] === 'number' ? metrics[key] as number : undefined;
  return <><PageHeader index="04" title="Factory Simulation" subtitle="Replay validated schedules or test controlled processing variability."><Button variant="primary" onClick={() => create.mutate()} disabled={!selectedSchedule || selectedSchedule.status !== 'completed' || create.isPending}><Play />{create.isPending ? 'Starting…' : 'Run simulation'}</Button></PageHeader>
    {actionError && <ErrorNotice error={actionError} retry={() => setActionError(null)} />}
    <div className="toolbar"><label className="field" style={{ minWidth: 260 }}><span className="field-label">Schedule</span><select value={selectedScheduleId || ''} onChange={(e) => { selectSchedule(e.target.value || null); setSelectedSimulationId(null); setPlaying(false); setIndex(0); }}><option value="">Select completed schedule</option>{schedules.filter((s) => s.status === 'completed').map((s) => <option key={s.id} value={s.id}>{s.algorithm.toUpperCase()} · {dateTime(s.created_at)}</option>)}</select></label><label className="field" style={{ minWidth: 160 }}><span className="field-label">Mode</span><select value={mode} onChange={(e) => setMode(e.target.value as 'playback' | 'stochastic')}><option value="playback">Deterministic playback</option><option value="stochastic">Stochastic experiment</option></select></label><label className="field" style={{ minWidth: 90 }}><span className="field-label">Random seed</span><input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} /></label>{mode === 'stochastic' && <><label className="field" style={{ minWidth: 95 }}><span className="field-label">Replications</span><input type="number" min="1" max="30" value={replications} onChange={(e) => setReplications(Number(e.target.value))} /></label><label className="field" style={{ minWidth: 135 }}><span className="field-label">Distribution</span><select value={distribution} onChange={(e) => setDistribution(e.target.value as typeof distribution)}><option value="fixed">Fixed</option><option value="triangular">Triangular</option><option value="lognormal">Lognormal</option></select></label><label className="field" style={{ minWidth: 110 }}><span className="field-label">Variability / CV</span><input type="number" min="0" max="1" step="0.05" value={variability} onChange={(e) => setVariability(Number(e.target.value))} /></label><label className="field" style={{ minWidth: 120 }}><span className="field-label">Failures / hour</span><input type="number" min="0" max="2" step="0.01" value={failureRate} onChange={(e) => setFailureRate(Number(e.target.value))} /></label><label className="field" style={{ minWidth: 115 }}><span className="field-label">Mean repair / min</span><input type="number" min="1" max="10080" value={meanRepair} onChange={(e) => setMeanRepair(Number(e.target.value))} /></label><label className="field" style={{ minWidth: 115 }}><span className="field-label">Horizon / h</span><input type="number" min="1" max={selectedSchedule?.horizon_minutes ? selectedSchedule.horizon_minutes / 60 : 720} step="0.5" value={horizonHours} onChange={(e) => setHorizonHours(Number(e.target.value))} /></label></>}</div>
    {selectedScheduleId && <div className="toolbar"><span className="toolbar-label">Saved simulation</span><select className="control" style={{ width: 290 }} aria-label="Saved simulation" value={selectedSimulationId || ''} onChange={(event) => { setSelectedSimulationId(event.target.value || null); setIndex(0); setPlaying(false); }}><option value="">Select a saved run</option>{savedQuery.data?.items.map((run) => <option key={run.id} value={run.id}>{run.id.slice(0, 8).toUpperCase()} · {run.status} · {dateTime(run.created_at)}</option>)}</select><span className="toolbar-spacer" />{simulation && <Status value={simulation.status} />}{simulation && !isFinished(simulation.status) && <Button variant="danger" size="small" onClick={() => cancel.mutate()} disabled={cancel.isPending}><Square />Cancel</Button>}</div>}
    {savedQuery.error && <ErrorNotice error={savedQuery.error} retry={() => savedQuery.refetch()} />}
    {simQuery.error && <ErrorNotice error={simQuery.error} retry={() => simQuery.refetch()} />}
    {eventsQuery.error && <ErrorNotice error={eventsQuery.error} retry={() => eventsQuery.refetch()} />}
    {simulation && !isFinished(simulation.status) && <div className="callout" role="status">Simulation is {simulation.status}. Event playback will be available after the run completes.</div>}
    {simulation?.status === 'failed' && <ErrorNotice error={simulation.diagnostics?.join(' · ') || 'The saved simulation failed. Check its configuration and source schedule.'} />}
    <div className="metric-grid"><Metric label="Completed jobs" value={number(metricValue('completed_jobs') ?? metricValue('completed_job_count'), observedReplications > 1 ? 1 : 0)} context={metricContext} /><Metric label="Throughput" value={number(metricValue('throughput_jobs_per_hour') ?? metricValue('throughput'), 2)} unit="jobs/h" context="Observation period" /><Metric label="Mean flow time" value={number(metricValue('mean_job_flow_minutes') ?? metricValue('mean_flow_time'), 1)} unit="min" context="Release to completion" /><Metric label="Mean waiting" value={number(metricValue('mean_job_waiting_minutes'), 1)} unit="min" context="Flow minus processing" /><Metric label="Late jobs" value={number(metricValue('late_jobs'), observedReplications > 1 ? 1 : 0)} context={metricContext} /><Metric label="WIP peak" value={number(metricValue('max_wip'), observedReplications > 1 ? 1 : 0)} unit="jobs" context={metricContext} /></div>
    <div className="toolbar"><div className="playback-controls" style={{ width: '100%' }}><Button size="small" onClick={() => setPlaying((x) => !x)} disabled={!events.length}>{playing ? <Pause /> : <Play />}{playing ? 'Pause' : 'Play'}</Button><Button size="small" onClick={() => { setPlaying(false); setIndex(0); }} disabled={!events.length}><RotateCcw />Reset</Button><Button size="small" onClick={() => { setPlaying(false); setIndex((n) => Math.min(events.length - 1, n + 1)); }} disabled={!events.length}><SkipForward />Step</Button><select className="control" style={{ width: 75 }} aria-label="Playback speed" value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>{[1, 2, 4, 8].map((n) => <option key={n} value={n}>{n}×</option>)}</select><input type="range" min="0" max={Math.max(0, events.length - 1)} value={Math.min(index, Math.max(0, events.length - 1))} onChange={(e) => { setPlaying(false); setIndex(Number(e.target.value)); }} aria-label="Simulation event position" disabled={!events.length} /><span className="mono tiny">{events.length ? `${index + 1} / ${events.length}` : 'NO EVENTS'}</span></div></div>
    {observedReplications > 1 && events.length > 0 && <div className="surface-note" style={{ marginBottom: 16 }}>Summary metrics are means across {observedReplications} replications. Factory playback and the event log show replication 1 only.</div>}
    {events.length ? <FactoryCanvas machines={playbackMachines} workCenters={bootstrap.work_centers} jobs={playbackJobs} events={events} eventIndex={index} origin={playbackSchedule?.origin} /> : <Panel title="Factory model"><Empty title="NO SIMULATION EVENTS" text="Select a completed schedule and run deterministic playback or a stochastic experiment." /></Panel>}
    {simulation?.status === 'completed' && <div className={observedReplications > 1 ? 'grid-2' : 'stack'} style={{ marginTop: 16, marginBottom: 16 }}>
      <Panel title="Work in progress" meta="Released but unfinished jobs / saved event stream"><div className="panel-body">{wipPoints.length ? <><EngineeringChart option={wipOption} label={`Work in progress through simulation time; first-replication peak ${number(firstReplicationWipPeak)} jobs`} height={275} /><p className="tiny muted" style={{ margin: '10px 0 0' }}>This step chart uses {observedReplications > 1 ? 'replication 1' : 'the recorded run'} (peak {number(firstReplicationWipPeak)} jobs). Its horizontal axis is elapsed minutes from the schedule origin; the vertical axis counts jobs.</p></> : <Empty title="NO WIP SERIES" text="The saved simulation has no released-job events to plot." />}</div></Panel>
      {observedReplications > 1 && <Panel title="Replication uncertainty" meta={`Means and approximate 95% intervals / ${observedReplications} replications`}><div className="panel-body">{intervalRows.length ? <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Metric</th><th>Sample mean</th><th>95% interval</th></tr></thead><tbody>{intervalRows.map((row) => <tr key={row.key}><td>{row.label}</td><td className="mono">{intervalValue(row.mean, row.unit)}</td><td className="mono">{intervalValue(row.lower, row.unit)} – {intervalValue(row.upper, row.unit)}</td></tr>)}</tbody></table></div> : <Empty title="NO UNCERTAINTY INTERVALS" text="The saved replications did not produce numeric values for interval estimation." />}<p className="tiny muted" style={{ margin: '10px 0 0' }}>Intervals use a normal approximation to replication means. Small replication counts and intervals extending beyond physical bounds need cautious interpretation.</p></div></Panel>}
    </div>}
    <Panel title="Simulation event log" meta={`${filteredEvents.length} shown · newest event first${observedReplications > 1 ? " · replication 1" : ""}`} className="" action={<div className="heading-actions"><select className="control" style={{ width: 120 }} aria-label="Filter event machine" value={machineFilter} onChange={(e) => setMachineFilter(e.target.value)}><option value="all">All machines</option>{playbackMachines.map((m) => <option key={m.id} value={m.id}>{m.machine_code}</option>)}</select><select className="control" style={{ width: 160 }} aria-label="Filter event type" value={eventFilter} onChange={(e) => setEventFilter(e.target.value)}><option value="all">All events</option>{Array.from(new Set(events.map((e) => e.event_type))).map((type) => <option value={type} key={type}>{type.replaceAll('_', ' ')}</option>)}</select></div>}><div className="event-log">{filteredEvents.length ? filteredEvents.map((event, i) => <div className="event-row" key={`${event.id || eventMinute(event)}-${i}`}><span>{timeLabel(event)}</span><span>{playbackJobs.find((j) => j.id === event.job_id)?.job_number || '—'}</span><span className="event-operation">{playbackMachines.find((m) => m.id === event.machine_id)?.machine_code || '—'}</span><span className="event-type">{event.event_type.replaceAll('_', ' ').toUpperCase()}</span></div>) : <Empty title="NO EVENTS AT THIS POSITION" text="Advance playback or adjust the log filters." />}</div></Panel>
    {mode === 'stochastic' && <p className="tiny muted" style={{ marginTop: 12 }}>Variability and failure rates are modeling assumptions entered for this experiment, not measured facility reliability statistics.</p>}
  </>;
}
