'use client';

import { createContext, useContext } from 'react';
import type { Bootstrap, Facility, ScheduleRun } from '@/lib/types';

export interface WorkspaceContextValue {
  bootstrap: Bootstrap;
  facilities: Facility[];
  schedules: ScheduleRun[];
  selectedSchedule: ScheduleRun | null;
  selectedScheduleId: string | null;
  selectSchedule: (id: string | null) => void;
  selectFacility: (id: string) => void;
  refresh: () => Promise<void>;
}
export const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);
export function useWorkspace() {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error('Workspace context is unavailable');
  return value;
}
