'use client';

import { useMemo, useState } from 'react';
import type { Machine, ProductionJob, SimulationEvent, WorkCenter } from '@/lib/types';
import { dateTime, number } from '@/lib/format';
import { Empty, Panel } from '@/components/ui';

export type MachinePlaybackState = { state: 'idle' | 'processing' | 'down' | 'unavailable'; job_id?: string; operation_id?: string; queue: number; completed: number };

export function machineStates(machines: Machine[], events: SimulationEvent[], index: number): Record<string, MachinePlaybackState> {
  const state: Record<string, MachinePlaybackState> = {};
  for (const machine of machines) state[machine.id] = { state: machine.active ? 'idle' : 'unavailable', queue: 0, completed: 0 };
  for (const event of events.slice(0, index + 1)) {
    if (!event.machine_id || !state[event.machine_id]) continue;
    const current = state[event.machine_id];
    if (event.event_type === 'queue_entered') current.queue += 1;
    if (event.event_type === 'operation_unfinished') current.queue = Math.max(0, current.queue - 1);
    if (event.event_type === 'processing_started') { current.state = 'processing'; current.job_id = event.job_id; current.operation_id = event.operation_id; current.queue = Math.max(0, current.queue - 1); }
    if (event.event_type === 'processing_completed') { current.state = 'idle'; current.job_id = undefined; current.operation_id = undefined; current.completed += 1; }
    if (event.event_type === 'machine_down') { current.state = 'down'; current.job_id = undefined; current.operation_id = undefined; }
    if (event.event_type === 'machine_available') current.state = 'idle';
  }
  return state;
}

function eventMinute(event: SimulationEvent): number {
  if (typeof event.time_min === 'number') return event.time_min;
  if (typeof event.simulation_time === 'number') return event.simulation_time;
  return 0;
}

export function FactoryCanvas({ machines, workCenters, jobs, events, eventIndex, origin, showInspector = true }: { machines: Machine[]; workCenters: WorkCenter[]; jobs: ProductionJob[]; events: SimulationEvent[]; eventIndex: number; origin?: string; showInspector?: boolean }) {
  const [selectedMachineId, setSelectedMachineId] = useState<string | null>(machines[0]?.id || null);
  const states = useMemo(() => machineStates(machines, events, eventIndex), [machines, events, eventIndex]);
  const selectedMachine = machines.find((machine) => machine.id === selectedMachineId);
  const selectedState = selectedMachineId ? states[selectedMachineId] : undefined;
  const currentEvent = events[eventIndex];
  const dateAt = currentEvent && origin ? new Date(new Date(origin).getTime() + eventMinute(currentEvent) * 60_000).toISOString() : undefined;
  const byCenter = workCenters.map((center) => ({ center, machines: machines.filter((machine) => machine.work_center_id === center.id) }));
  return <div className={showInspector ? 'layout-split' : ''}><Panel title="Factory model" meta={currentEvent ? `Simulation time ${dateAt ? dateTime(dateAt) : `${number(eventMinute(currentEvent), 1)} min`} · Event ${eventIndex + 1} / ${events.length}` : 'Select a simulation event to inspect machine state'}><div className="factory-grid">{byCenter.map(({ center, machines: centerMachines }) => <div className="factory-center" key={center.id}><div className="factory-center-heading">{center.code} / {center.name}</div><div className="factory-machines">{centerMachines.map((machine) => { const state = states[machine.id]; const job = jobs.find((item) => item.id === state?.job_id); return <button key={machine.id} className={`factory-machine ${state?.state || 'idle'} ${selectedMachineId === machine.id ? 'selected' : ''}`} onClick={() => setSelectedMachineId(machine.id)} aria-label={`Inspect ${machine.machine_code}`}><strong>{machine.machine_code}</strong><span className="machine-state">{state?.state || 'idle'} · Q {state?.queue || 0}</span><div className="machine-job">{job ? `${job.job_number} processing` : state?.state === 'down' ? 'Downtime active' : 'No active operation'}</div></button>; })}</div></div>)}</div><div className="panel-body" style={{ paddingTop: 0 }}><div className="surface-note">Machine states and queues are reconstructed from the saved simulation event stream. The layout represents configured work centers, not live equipment telemetry.</div></div></Panel>{showInspector && <Panel title="Machine inspector" meta={selectedMachine?.machine_code || 'Select a resource'}><div className="panel-body">{selectedMachine && selectedState ? <dl className="detail-list"><dt>Machine</dt><dd>{selectedMachine.machine_code}</dd><dt>Work center</dt><dd>{workCenters.find((c) => c.id === selectedMachine.work_center_id)?.code || 'N/A'}</dd><dt>Current state</dt><dd>{selectedState.state.toUpperCase()}</dd><dt>Queue length</dt><dd>{selectedState.queue}</dd><dt>Completed operations</dt><dd>{selectedState.completed}</dd><dt>Current job</dt><dd>{jobs.find((j) => j.id === selectedState.job_id)?.job_number || 'None'}</dd><dt>Operating cost</dt><dd>${number(selectedMachine.hourly_operating_cost, 2)}/h</dd></dl> : <Empty title="NO MACHINE SELECTED" text="Select a machine on the factory layout." />}</div></Panel>}</div>;
}
