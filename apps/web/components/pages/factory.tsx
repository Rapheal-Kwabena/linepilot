'use client';

import { useEffect, useRef } from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { ArrowRight } from 'lucide-react';
import { api } from '@/lib/api';
import { dateTime } from '@/lib/format';
import { displayJobs, displayMachines } from '@/lib/snapshot';
import { useWorkspace } from '@/components/workspace-context';
import { useUi } from '@/stores/ui';
import { isFinished } from '@/lib/types';
import { FactoryCanvas } from '@/components/simulation/factory-canvas';
import { ErrorNotice, PageHeader, Panel, Status } from '@/components/ui';

export function FactoryPage() {
  const { bootstrap, schedules, selectedSchedule, selectedScheduleId, selectSchedule } = useWorkspace();
  const { selectedSimulationId, setSelectedSimulationId } = useUi();
  const restoredSchedule = useRef<string | null>(null);
  const savedQuery = useQuery({
    queryKey: ['simulations', selectedScheduleId],
    queryFn: () => api.simulations(selectedScheduleId!),
    enabled: !!selectedScheduleId,
    refetchInterval: (query) => query.state.data?.items.some((item) => !isFinished(item.status)) ? 1500 : false,
  });
  useEffect(() => {
    if (!selectedScheduleId || !savedQuery.data || restoredSchedule.current === selectedScheduleId) return;
    restoredSchedule.current = selectedScheduleId;
    if (!selectedSimulationId) {
      const latest = savedQuery.data.items.find((run) => run.status === 'completed') || savedQuery.data.items[0];
      if (latest) setSelectedSimulationId(latest.id);
    }
  }, [savedQuery.data, selectedScheduleId, selectedSimulationId, setSelectedSimulationId]);

  const query = useQuery({
    queryKey: ['simulation', selectedSimulationId],
    queryFn: () => api.simulation(selectedSimulationId!),
    enabled: !!selectedSimulationId,
    refetchInterval: (state) => state.state.data && !isFinished(state.state.data.status) ? 1300 : false,
  });
  const sourceScheduleQuery = useQuery({
    queryKey: ['schedule', query.data?.schedule_run_id],
    queryFn: () => api.schedule(query.data!.schedule_run_id),
    enabled: !!query.data?.schedule_run_id && query.data.schedule_run_id !== selectedSchedule?.id,
  });
  const eventsQuery = useQuery({
    queryKey: ['simulation-events', selectedSimulationId],
    queryFn: () => api.simulationEvents(selectedSimulationId!),
    enabled: !!selectedSimulationId && query.data?.status === 'completed' && !query.data.events?.length,
  });
  const events = query.data?.events?.length ? query.data.events : eventsQuery.data?.items || [];
  const playbackSchedule = query.data?.schedule_run_id === selectedSchedule?.id ? selectedSchedule : sourceScheduleQuery.data;
  const playbackMachines = displayMachines(playbackSchedule || null, bootstrap.machines);
  const playbackJobs = displayJobs(playbackSchedule || null, bootstrap.jobs);

  return <>
    <PageHeader index="05" title="Factory Model" subtitle="Work-center layout and resource states reconstructed from simulation events."><Link className="button primary" href="/simulation">Open simulation<ArrowRight size={16} /></Link></PageHeader>
    <div className="toolbar">
      <span className="toolbar-label">Facility</span><span className="tag">{bootstrap.facility.facility_name}</span>
      <label className="field" style={{ minWidth: 245 }}><span className="field-label">Schedule</span><select value={selectedScheduleId || ''} onChange={(event) => { selectSchedule(event.target.value || null); setSelectedSimulationId(null); }} aria-label="Factory schedule"><option value="">Select completed schedule</option>{schedules.filter((run) => run.status === 'completed').map((run) => <option value={run.id} key={run.id}>{run.algorithm.toUpperCase()} · {dateTime(run.created_at)}</option>)}</select></label>
      <label className="field" style={{ minWidth: 255 }}><span className="field-label">Saved simulation</span><select value={selectedSimulationId || ''} onChange={(event) => setSelectedSimulationId(event.target.value || null)} aria-label="Factory saved simulation"><option value="">Configured layout</option>{savedQuery.data?.items.map((run) => <option value={run.id} key={run.id}>{run.id.slice(0, 8).toUpperCase()} · {run.status} · {dateTime(run.created_at)}</option>)}</select></label>
      <span className="toolbar-spacer" /><span className="tiny muted">{bootstrap.work_centers.length} work centers / {playbackMachines.length} machines</span>
    </div>
    {savedQuery.error && <ErrorNotice error={savedQuery.error} retry={() => savedQuery.refetch()} />}
    {query.error && <ErrorNotice error={query.error} retry={() => query.refetch()} />}
    {sourceScheduleQuery.error && <ErrorNotice error={sourceScheduleQuery.error} retry={() => sourceScheduleQuery.refetch()} />}
    {eventsQuery.error && <ErrorNotice error={eventsQuery.error} retry={() => eventsQuery.refetch()} />}
    {query.data && !isFinished(query.data.status) && <div className="callout" role="status"><Status value={query.data.status} /> Simulation is running. The factory view will update when its event stream is saved.</div>}
    {query.data?.status === 'failed' && <ErrorNotice error={query.data.diagnostics?.join(' · ') || 'The selected simulation failed.'} />}
    {events.length ? <><div className="callout" style={{ marginBottom: 16 }}>Showing final state from simulation <span className="mono">{selectedSimulationId?.slice(0, 8).toUpperCase()}</span>. Use Simulation to step through the event sequence.</div><FactoryCanvas machines={playbackMachines} workCenters={bootstrap.work_centers} jobs={playbackJobs} events={events} eventIndex={events.length - 1} origin={playbackSchedule?.origin} /></> : <Panel title="Configured factory layout" meta="Static resource configuration"><div className="factory-grid">{bootstrap.work_centers.map((center) => <div className="factory-center" key={center.id}><div className="factory-center-heading">{center.code} / {center.name}</div><div className="factory-machines">{bootstrap.machines.filter((machine) => machine.work_center_id === center.id).map((machine) => <div className="factory-machine" key={machine.id}><strong>{machine.machine_code}</strong><span className="machine-state">{machine.status} · {machine.active ? 'active' : 'inactive'}</span><div className="machine-job">${machine.hourly_operating_cost}/h</div></div>)}</div></div>)}</div><div className="panel-body"><div className="surface-note">Configured machines are shown here. Select a saved simulation or run a new one to inspect event-driven processing, queues and completed work.</div></div></Panel>}
  </>;
}
