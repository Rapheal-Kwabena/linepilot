'use client';

import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { ArrowRight, Play, Plus } from 'lucide-react';
import { api } from '@/lib/api';
import { dateTime, isoToLocalInput, localInputToIso, metric, number, percent } from '@/lib/format';
import type { ScenarioModification } from '@/lib/types';
import { isFinished } from '@/lib/types';
import { useWorkspace } from '@/components/workspace-context';
import { Button, Empty, ErrorNotice, PageHeader, Panel, Status } from '@/components/ui';

const scenarioTypes = [
  { value: 'machine_failure', label: 'Machine failure', detail: 'Block one machine for a specified interval.' },
  { value: 'add_capacity', label: 'Add production capacity', detail: 'Clone a resource and its eligible operations.' },
  { value: 'processing_change', label: 'Processing improvement', detail: 'Scale one operation’s processing time.' },
  { value: 'order_surge', label: 'Order surge', detail: 'Add copies of a production job.' },
  { value: 'priority_change', label: 'Priority adjustment', detail: 'Change one job’s delivery weight.' },
  { value: 'shift_adjustment', label: 'Shift adjustment', detail: 'Add or close an operating interval.' },
] as const;
type ScenarioType = typeof scenarioTypes[number]['value'];
const comparisonRows = [
  { label: 'Makespan', keys: ['makespan_minutes', 'makespan'], unit: 'min', desirable: -1 },
  { label: 'Total tardiness', keys: ['total_tardiness_minutes', 'total_tardiness'], unit: 'min', desirable: -1 },
  { label: 'Throughput', keys: ['throughput_jobs_per_hour'], unit: 'jobs/h', desirable: 1 },
  { label: 'Late jobs', keys: ['late_jobs'], unit: 'jobs', desirable: -1 },
  { label: 'On-time delivery', keys: ['on_time_delivery_rate', 'on_time_rate'], unit: '%', desirable: 1 },
  { label: 'Average utilization', keys: ['average_machine_utilization'], unit: '%', desirable: 0 },
  { label: 'Operating cost', keys: ['operating_cost'], unit: '$', desirable: -1 },
] as const;
function display(value: number | undefined, unit: string) { if (value === undefined) return 'N/A'; if (unit === '%') return percent(value); if (unit === '$') return `$${number(value, 2)}`; return `${number(value)} ${unit}`; }

export function ScenariosPage() {
  const { bootstrap, selectedSchedule, schedules, selectSchedule, refresh } = useWorkspace();
  const baseRuns = schedules.filter((run) => run.status === 'completed' && !run.scenario_id);
  const [type, setType] = useState<ScenarioType>('machine_failure');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [baselineId, setBaselineId] = useState(!selectedSchedule?.scenario_id ? selectedSchedule?.id || '' : '');
  const [machineId, setMachineId] = useState(bootstrap.machines[0]?.id || '');
  const [startTime, setStartTime] = useState(isoToLocalInput(selectedSchedule?.origin ? new Date(new Date(selectedSchedule.origin).getTime() + 2 * 3600_000).toISOString() : undefined));
  const [duration, setDuration] = useState(90);
  const [newMachineCode, setNewMachineCode] = useState('');
  const [hourlyCost, setHourlyCost] = useState(50);
  const [operationId, setOperationId] = useState(bootstrap.jobs[0]?.operations[0]?.id || '');
  const [multiplier, setMultiplier] = useState(.9);
  const [templateJobId, setTemplateJobId] = useState(bootstrap.jobs[0]?.id || '');
  const [surgeCount, setSurgeCount] = useState(1);
  const [priorityJobId, setPriorityJobId] = useState(bootstrap.jobs[0]?.id || '');
  const [newPriority, setNewPriority] = useState(7);
  const [shiftMode, setShiftMode] = useState<'add' | 'close'>('add');
  const [algorithm, setAlgorithm] = useState('fcfs');
  const [objective, setObjective] = useState('makespan');
  const [selectedScenarioId, setSelectedScenarioId] = useState('');
  const [formError, setFormError] = useState('');
  const [actionError, setActionError] = useState<unknown>(null);
  const scenariosQuery = useQuery({ queryKey: ['scenarios', bootstrap.facility.id], queryFn: () => api.scenarios(bootstrap.facility.id) });
  const baselineDetailQuery = useQuery({ queryKey: ['schedule', baselineId], queryFn: () => api.schedule(baselineId), enabled: !!baselineId });
  const snapshot = baselineDetailQuery.data?.snapshot;
  const machineIds = useMemo(() => new Set(snapshot?.machines.map((machine) => machine.id)), [snapshot]);
  const jobIds = useMemo(() => new Set(snapshot?.jobs.map((job) => job.id)), [snapshot]);
  const operationIds = useMemo(() => new Set(snapshot?.jobs.flatMap((job) => job.operations.map((operation) => operation.id))), [snapshot]);
  const availableMachines = useMemo(() => snapshot ? bootstrap.machines.filter((machine) => machineIds.has(machine.id)) : bootstrap.machines.filter((machine) => machine.active && machine.status === 'available'), [snapshot, bootstrap.machines, machineIds]);
  const availableJobs = useMemo(() => snapshot ? bootstrap.jobs.filter((job) => jobIds.has(job.id)) : bootstrap.jobs.filter((job) => ['planned', 'released'].includes(job.status)), [snapshot, bootstrap.jobs, jobIds]);
  const availableOperations = useMemo(() => availableJobs.flatMap((job) => job.operations.filter((operation) => !snapshot || operationIds.has(operation.id)).map((operation) => ({ job, operation }))), [availableJobs, snapshot, operationIds]);
  const scenarios = scenariosQuery.data?.items ?? bootstrap.scenarios;
  const selectedScenario = scenarios.find((scenario) => scenario.id === selectedScenarioId);
  const baseline = schedules.find((run) => run.id === (selectedScenario?.baseline_schedule_id || baselineId));
  const scenarioRuns = schedules.filter((run) => run.scenario_id === selectedScenarioId);
  const scenarioRun = scenarioRuns[0] || null;
  const firstBaseRunId = baseRuns[0]?.id;
  const baselineOrigin = schedules.find((item) => item.id === baselineId)?.origin;
  const scenarioRunId = scenarioRun?.id;
  const scenarioRunStatus = scenarioRun?.status;
  useEffect(() => { if (firstBaseRunId && !baselineId) setBaselineId(firstBaseRunId); }, [firstBaseRunId, baselineId]);
  useEffect(() => { if (baselineOrigin) setStartTime(isoToLocalInput(new Date(new Date(baselineOrigin).getTime() + 2 * 3600_000).toISOString())); }, [baselineId, baselineOrigin]);
  useEffect(() => {
    if (availableMachines.length && !availableMachines.some((machine) => machine.id === machineId)) setMachineId(availableMachines[0].id);
    if (availableJobs.length && !availableJobs.some((job) => job.id === templateJobId)) setTemplateJobId(availableJobs[0].id);
    if (availableJobs.length && !availableJobs.some((job) => job.id === priorityJobId)) setPriorityJobId(availableJobs[0].id);
    if (availableOperations.length && !availableOperations.some(({ operation }) => operation.id === operationId)) setOperationId(availableOperations[0].operation.id);
  }, [availableMachines, availableJobs, availableOperations, machineId, templateJobId, priorityJobId, operationId]);

  const modification = (): ScenarioModification => {
    if (type === 'machine_failure' || type === 'shift_adjustment') {
      if (!availableMachines.some((machine) => machine.id === machineId)) throw new Error('Select a machine from the baseline schedule.');
      if (!startTime || !Number.isFinite(duration) || duration <= 0) throw new Error('Enter a valid start and positive duration.');
      const start = new Date(localInputToIso(startTime));
      const times = { start_time: start.toISOString(), end_time: new Date(start.getTime() + duration * 60_000).toISOString() };
      return type === 'machine_failure' ? { type, machine_id: machineId, ...times } : { type, machine_id: machineId, mode: shiftMode, ...times };
    }
    if (type === 'add_capacity') {
      if (!availableMachines.some((machine) => machine.id === machineId)) throw new Error('Select a machine from the baseline schedule to clone.');
      if (!Number.isFinite(hourlyCost) || hourlyCost < 0) throw new Error('Hourly cost must be nonnegative.');
      return { type, source_machine_id: machineId, machine_code: newMachineCode.trim() || undefined, hourly_cost: hourlyCost };
    }
    if (type === 'processing_change') {
      if (!availableOperations.some(({ operation }) => operation.id === operationId)) throw new Error('Select an operation from the baseline schedule.');
      if (!Number.isFinite(multiplier) || multiplier <= 0 || multiplier > 10) throw new Error('Processing multiplier must be above zero and no more than 10.');
      return { type, operation_id: operationId, multiplier };
    }
    if (type === 'order_surge') {
      if (!availableJobs.some((job) => job.id === templateJobId)) throw new Error('Select a template job from the baseline schedule.');
      if (!Number.isInteger(surgeCount) || surgeCount < 1 || surgeCount > 20) throw new Error('Order count must be between 1 and 20.');
      return { type, template_job_id: templateJobId, count: surgeCount };
    }
    if (!availableJobs.some((job) => job.id === priorityJobId)) throw new Error('Select a job from the baseline schedule.');
    if (!Number.isInteger(newPriority) || newPriority < 1 || newPriority > 10) throw new Error('Priority must be between 1 and 10.');
    return { type: 'priority_change', job_id: priorityJobId, priority: newPriority };
  };
  const create = useMutation({ mutationFn: async () => {
    if (!name.trim()) throw new Error('Enter a scenario name.');
    if (!baselineId) throw new Error('Select a completed baseline schedule.');
    return api.createScenario({ facility_id: bootstrap.facility.id, name: name.trim(), description: description.trim(), baseline_schedule_id: baselineId, modifications: [modification()] });
  }, onSuccess: async (scenario) => { setSelectedScenarioId(scenario.id); setName(''); setDescription(''); setFormError(''); await Promise.all([scenariosQuery.refetch(), refresh()]); }, onError: (error) => { setActionError(error); setFormError(error instanceof Error ? error.message : String(error)); } });
  const evaluate = useMutation({ mutationFn: () => api.evaluateScenario(selectedScenarioId, { algorithm, objective }), onSuccess: async (run) => { selectSchedule(run.id); await refresh(); }, onError: setActionError });
  useEffect(() => { if (scenarioRunId && isFinished(scenarioRunStatus)) void refresh(); }, [scenarioRunId, scenarioRunStatus, refresh]);
  const typeDetail = scenarioTypes.find((item) => item.value === type)?.detail;

  return <><PageHeader index="06" title="Scenario Lab" subtitle="Test changed production conditions against an immutable saved baseline."><Button variant="primary" onClick={() => create.mutate()} disabled={create.isPending}><Plus />{create.isPending ? 'Saving…' : 'Create scenario'}</Button></PageHeader>
    {actionError !== null && <ErrorNotice error={actionError} retry={() => setActionError(null)} />}
    <div className="grid-2"><Panel title="Build a scenario" meta="One controlled change per scenario"><div className="panel-body"><div className="stack"><div className="form-grid"><div className="field span-2"><label htmlFor="scenario-name">Scenario name</label><input id="scenario-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Add a second milling resource" /></div><div className="field span-2"><label htmlFor="scenario-desc">Description</label><input id="scenario-desc" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Operational question or assumption" /></div><div className="field span-2"><label htmlFor="scenario-baseline">Baseline schedule</label><select id="scenario-baseline" value={baselineId} onChange={(e) => setBaselineId(e.target.value)}><option value="">Choose a completed schedule</option>{baseRuns.map((run) => <option key={run.id} value={run.id}>{run.algorithm.toUpperCase()} · {dateTime(run.created_at)}</option>)}</select></div><div className="field span-2"><label htmlFor="scenario-type">Scenario type</label><select id="scenario-type" value={type} onChange={(e) => { setType(e.target.value as ScenarioType); setFormError(''); }}>{scenarioTypes.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select><span className="tiny muted">{typeDetail}</span></div>
      {['machine_failure', 'add_capacity', 'shift_adjustment'].includes(type) && <div className="field"><label htmlFor="scenario-machine">{type === 'add_capacity' ? 'Source machine' : 'Machine'}</label><select id="scenario-machine" value={machineId} onChange={(e) => setMachineId(e.target.value)}>{availableMachines.map((machine) => <option key={machine.id} value={machine.id}>{machine.machine_code} · {machine.machine_name}</option>)}</select></div>}
      {type === 'add_capacity' && <><div className="field"><label htmlFor="scenario-new-code">New machine code</label><input id="scenario-new-code" value={newMachineCode} onChange={(e) => setNewMachineCode(e.target.value)} placeholder="Optional; generated if empty" /></div><div className="field"><label htmlFor="scenario-new-cost">Hourly cost / $</label><input id="scenario-new-cost" type="number" min="0" step="0.01" value={hourlyCost} onChange={(e) => setHourlyCost(Number(e.target.value))} /></div></>}
      {['machine_failure', 'shift_adjustment'].includes(type) && <><div className="field"><label htmlFor="scenario-start">Interval begins / {bootstrap.facility.timezone}</label><input id="scenario-start" type="datetime-local" value={startTime} onChange={(e) => setStartTime(e.target.value)} /></div><div className="field"><label htmlFor="scenario-duration">Duration / minutes</label><input id="scenario-duration" type="number" min="1" max="10080" value={duration} onChange={(e) => setDuration(Number(e.target.value))} /></div></>}
      {type === 'shift_adjustment' && <div className="field"><label htmlFor="scenario-shift-mode">Shift change</label><select id="scenario-shift-mode" value={shiftMode} onChange={(e) => setShiftMode(e.target.value as 'add' | 'close')}><option value="add">Add operating time</option><option value="close">Close operating time</option></select></div>}
      {type === 'processing_change' && <><div className="field span-2"><label htmlFor="scenario-operation">Production operation</label><select id="scenario-operation" value={operationId} onChange={(e) => setOperationId(e.target.value)}>{availableOperations.map(({ job, operation }) => <option key={operation.id} value={operation.id}>{job.job_number} / OP {operation.operation_number} / {operation.operation_name}</option>)}</select></div><div className="field"><label htmlFor="scenario-multiplier">Processing-time multiplier</label><input id="scenario-multiplier" type="number" min="0.01" max="10" step="0.05" value={multiplier} onChange={(e) => setMultiplier(Number(e.target.value))} /><span className="tiny muted">0.90 = 10% faster; 1.10 = 10% slower</span></div></>}
      {type === 'order_surge' && <><div className="field"><label htmlFor="scenario-template-job">Template job</label><select id="scenario-template-job" value={templateJobId} onChange={(e) => setTemplateJobId(e.target.value)}>{availableJobs.map((job) => <option key={job.id} value={job.id}>{job.job_number} / {job.product_name}</option>)}</select></div><div className="field"><label htmlFor="scenario-count">Additional orders</label><input id="scenario-count" type="number" min="1" max="20" value={surgeCount} onChange={(e) => setSurgeCount(Number(e.target.value))} /></div></>}
      {type === 'priority_change' && <><div className="field"><label htmlFor="scenario-priority-job">Job</label><select id="scenario-priority-job" value={priorityJobId} onChange={(e) => setPriorityJobId(e.target.value)}>{availableJobs.map((job) => <option key={job.id} value={job.id}>{job.job_number} / current {job.priority}</option>)}</select></div><div className="field"><label htmlFor="scenario-priority">New priority / weight</label><input id="scenario-priority" type="number" min="1" max="10" value={newPriority} onChange={(e) => setNewPriority(Number(e.target.value))} /></div></>}
      </div>{formError && <div className="field-error" role="alert">{formError}</div>}<div className="surface-note">The scenario stores a separate input snapshot. Facility jobs, machines and the baseline schedule are unchanged.</div></div></div></Panel>
    <Panel title="Saved scenarios" meta={`${scenarios.length} scenario${scenarios.length === 1 ? '' : 's'}`}><div className="data-table-wrap">{scenarios.length ? <table className="data-table"><thead><tr><th>Scenario</th><th>Modification</th><th>Created</th><th>Inspect</th></tr></thead><tbody>{scenarios.map((scenario) => <tr key={scenario.id} className={selectedScenarioId === scenario.id ? 'selected' : ''}><td><button className="button ghost small" onClick={() => setSelectedScenarioId(scenario.id)}>{scenario.name}</button><div className="tiny muted">{scenario.description}</div></td><td className="mono">{scenario.modifications?.map((mod) => String(mod.type).replaceAll('_', ' ')).join(', ') || 'None'}</td><td className="mono">{dateTime(scenario.created_at)}</td><td><Button size="small" onClick={() => setSelectedScenarioId(scenario.id)}>{selectedScenarioId === scenario.id ? 'Selected' : 'Select'}</Button></td></tr>)}</tbody></table> : <Empty title="NO SCENARIOS" text="Create a scenario to measure how a changed operating assumption affects production." />}</div></Panel></div>
    {selectedScenario && <><div className="toolbar" style={{ marginTop: 16 }}><span className="toolbar-label">Selected scenario</span><strong>{selectedScenario.name}</strong><span className="toolbar-spacer" /><label className="field" style={{ minWidth: 125 }}><span className="field-label">Algorithm</span><select value={algorithm} onChange={(e) => setAlgorithm(e.target.value)}><option value="fcfs">FCFS</option><option value="spt">SPT</option><option value="edd">EDD</option><option value="cp_sat">CP-SAT</option></select></label><label className="field" style={{ minWidth: 170 }}><span className="field-label">Objective</span><select value={objective} onChange={(e) => setObjective(e.target.value)}><option value="makespan">Makespan</option><option value="total_tardiness">Total tardiness</option><option value="weighted_tardiness">Weighted tardiness</option><option value="balanced">Balanced</option></select></label><Button variant="primary" onClick={() => evaluate.mutate()} disabled={evaluate.isPending || !!scenarioRun && !isFinished(scenarioRun.status) || !baseline}><Play />{evaluate.isPending ? 'Starting…' : 'Evaluate scenario'}</Button></div>
      <Panel title="Scenario comparison" meta="Baseline data versus modified input snapshot"><div className="panel-body">{scenarioRun && baseline && scenarioRun.status === 'completed' ? <div className="data-table-wrap"><table className="compare-table"><thead><tr><th>Metric</th><th>Baseline / {baseline.algorithm.toUpperCase()}</th><th>Scenario / {scenarioRun.algorithm.toUpperCase()}</th><th>Difference</th></tr></thead><tbody>{comparisonRows.map((row) => { const before = metric(baseline.metrics, ...row.keys); const after = metric(scenarioRun.metrics, ...row.keys); const delta = before !== undefined && after !== undefined ? after - before : undefined; return <tr key={row.label}><td>{row.label}</td><td>{display(before, row.unit)}</td><td>{display(after, row.unit)}</td><td className={delta !== undefined && row.desirable !== 0 ? delta * row.desirable > 0 ? 'success-text' : delta * row.desirable < 0 ? 'critical-text' : '' : ''}>{delta === undefined ? 'N/A' : `${delta > 0 ? '+' : ''}${display(delta, row.unit)}`}</td></tr>; })}</tbody></table></div> : scenarioRun && !isFinished(scenarioRun.status) ? <div role="status" className="callout"><Status value={scenarioRun.status} /> Scenario run is executing. Results will appear after validation.</div> : <Empty title="SCENARIO NOT EVALUATED" text="Evaluate the scenario to compare measured makespan, tardiness, utilization and cost with its saved baseline." action={baseline ? <Button variant="primary" onClick={() => evaluate.mutate()} disabled={evaluate.isPending}>Evaluate now<ArrowRight /></Button> : undefined} />}</div></Panel>
      {scenarioRun?.status === 'failed' && <ErrorNotice error={Array.isArray(scenarioRun.diagnostics) ? scenarioRun.diagnostics.join(' · ') : 'Scenario schedule failed. Check modifications and scheduling horizon.'} />}
      <div className="surface-note" style={{ marginTop: 12 }}>This comparison intentionally changes the input snapshot. A lower makespan or tardiness is preferable; utilization needs queue and throughput context.</div>
    </>}
  </>;
}
