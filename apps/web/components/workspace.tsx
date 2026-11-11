'use client';

import { useCallback, useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, api, toItems } from '@/lib/api';
import { isFinished } from '@/lib/types';
import { useUi } from '@/stores/ui';
import { setDisplayTimeZone } from '@/lib/format';
import { WorkspaceContext } from './workspace-context';
import { AppShell } from './layout/app-shell';
import { ErrorNotice, Loading } from './ui';
import { OverviewPage } from './pages/overview';
import { PlannerPage } from './pages/planner';
import { SchedulingPage } from './pages/scheduling';
import { SimulationPage } from './pages/simulation';
import { FactoryPage } from './pages/factory';
import { BottlenecksPage } from './pages/bottlenecks';
import { UtilizationPage } from './pages/utilization';
import { ScenariosPage } from './pages/scenarios';
import { ReportsPage } from './pages/reports';
import { MachinesPage } from './pages/machines';
import { ImportsPage } from './pages/imports';
import { SettingsPage } from './pages/settings';
import { HelpPage } from './pages/help';

const pages: Record<string, React.ComponentType> = {
  overview: OverviewPage, planner: PlannerPage, scheduling: SchedulingPage,
  jobs: PlannerPage,
  simulation: SimulationPage, factory: FactoryPage, bottlenecks: BottlenecksPage,
  utilization: UtilizationPage, scenarios: ScenariosPage, reports: ReportsPage,
  machines: MachinesPage, imports: ImportsPage, settings: SettingsPage, help: HelpPage,
};

export function Workspace({ module }: { module: string }) {
  const client = useQueryClient();
  const { selectedFacilityId, facilityPreferenceLoaded, loadFacilityPreference, setSelectedFacilityId, selectedScheduleId, setSelectedScheduleId, theme, setTheme } = useUi();
  const bootstrapQuery = useQuery({ queryKey: ['bootstrap', selectedFacilityId], queryFn: () => api.bootstrap(selectedFacilityId), enabled: facilityPreferenceLoaded });
  const facilitiesQuery = useQuery({ queryKey: ['facilities'], queryFn: api.facilities, enabled: facilityPreferenceLoaded });
  const activeFacilityId = bootstrapQuery.data?.facility.id;
  const schedulesQuery = useQuery({ queryKey: ['schedules', activeFacilityId], queryFn: () => api.schedules(activeFacilityId!), enabled: !!activeFacilityId, refetchInterval: (query) => {
    const runs = query.state.data?.items || [];
    return runs.some((run) => !isFinished(run.status)) ? 1500 : false;
  } });
  const scheduleId = selectedScheduleId || bootstrapQuery.data?.latest_schedule?.id || schedulesQuery.data?.items?.[0]?.id || null;
  const selectedQuery = useQuery({ queryKey: ['schedule', scheduleId], queryFn: () => api.schedule(scheduleId!), enabled: !!scheduleId, refetchInterval: (query) => query.state.data && !isFinished(query.state.data.status) ? 1200 : false });
  const completedRunId = selectedQuery.data && isFinished(selectedQuery.data.status) ? selectedQuery.data.id : null;
  const refresh = useCallback(async () => { await Promise.all([client.invalidateQueries({ queryKey: ['bootstrap'] }), client.invalidateQueries({ queryKey: ['schedules'] }), client.invalidateQueries({ queryKey: ['schedule'] })]); }, [client]);
  useEffect(() => { const saved = localStorage.getItem('linepilot-theme'); setTheme(saved === 'dark' ? 'dark' : 'light'); }, [setTheme]);
  useEffect(() => { if (!facilityPreferenceLoaded) loadFacilityPreference(); }, [facilityPreferenceLoaded, loadFacilityPreference]);
  useEffect(() => { if (selectedFacilityId && bootstrapQuery.error instanceof ApiError && bootstrapQuery.error.status === 404) setSelectedFacilityId(null); }, [selectedFacilityId, bootstrapQuery.error, setSelectedFacilityId]);
  useEffect(() => {
    if (completedRunId) {
      client.invalidateQueries({ queryKey: ['schedules'] });
      client.invalidateQueries({ queryKey: ['bootstrap'] });
    }
  }, [completedRunId, client]);
  void theme;
  const Page = pages[module];
  if (!Page) return <main className="content"><h1>MODULE NOT FOUND</h1><p>The requested module does not exist.</p></main>;
  if (!facilityPreferenceLoaded || bootstrapQuery.isLoading) return <div className="app-shell"><main className="content" style={{ width: '100%', paddingTop: 60 }}><div className="eyebrow">LINEPILOT / CONNECTING</div><h1>OPERATIONS SYSTEM</h1><div className="panel"><Loading label="Loading facility data" /></div></main></div>;
  if (bootstrapQuery.error || !bootstrapQuery.data) return <div className="app-shell"><main className="content" style={{ width: '100%', paddingTop: 60 }}><div className="eyebrow">LINEPILOT / CONNECTION ERROR</div><h1>WORKSPACE UNAVAILABLE</h1><ErrorNotice error={bootstrapQuery.error} retry={() => bootstrapQuery.refetch()} /><p className="muted">Start the LINEPILOT API, then retry. Operational data is never replaced by a sample response when the API is unavailable.</p></main></div>;
  setDisplayTimeZone(bootstrapQuery.data.facility.timezone);
  const facilities = [...toItems(facilitiesQuery.data)];
  if (!facilities.some((facility) => facility.id === bootstrapQuery.data.facility.id)) facilities.push(bootstrapQuery.data.facility);
  const schedules = schedulesQuery.data?.items || bootstrapQuery.data.schedules || [];
  const selectedSchedule = selectedQuery.data || schedules.find((run) => run.id === scheduleId) || bootstrapQuery.data.latest_schedule || null;
  const selectFacility = (id: string) => { if (id !== bootstrapQuery.data.facility.id) setSelectedFacilityId(id); };
  return <WorkspaceContext.Provider value={{ bootstrap: bootstrapQuery.data, facilities, schedules, selectedSchedule, selectedScheduleId: scheduleId, selectSchedule: setSelectedScheduleId, selectFacility, refresh }}><AppShell key={bootstrapQuery.data.facility.id} module={module}><Page /></AppShell></WorkspaceContext.Provider>;
}
