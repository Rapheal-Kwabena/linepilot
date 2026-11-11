import type { Machine, ProductionJob, ScheduleRun } from './types';

export function displayMachines(schedule: ScheduleRun | null, base: Machine[]): Machine[] {
  const snapshot = schedule?.snapshot;
  if (!snapshot) return base;
  const origin = new Date(snapshot.origin).getTime();
  return snapshot.machines.map((item) => {
    const existing = base.find((machine) => machine.id === item.id);
    return {
      id: item.id, machine_code: item.code, machine_name: existing?.machine_name || `${item.code} (scenario resource)`,
      machine_type: existing?.machine_type || 'scenario', work_center_id: item.work_center_id || existing?.work_center_id || '',
      hourly_operating_cost: item.hourly_cost ?? existing?.hourly_operating_cost ?? 0,
      status: 'available', active: true, operating_calendar_id: existing?.operating_calendar_id,
      downtime: item.downtime.map(([start, end], index) => ({ id: `${item.id}-${index}`, start_time: new Date(origin + start * 60_000).toISOString(), end_time: new Date(origin + end * 60_000).toISOString(), reason: 'Unavailable in saved run' })),
    };
  });
}

export function displayJobs(schedule: ScheduleRun | null, base: ProductionJob[]): ProductionJob[] {
  const snapshot = schedule?.snapshot;
  if (!snapshot) return base;
  const origin = new Date(snapshot.origin).getTime();
  return snapshot.jobs.map((item) => {
    const existing = base.find((job) => job.id === item.id);
    return {
      id: item.id, job_number: item.job_number, customer_reference: existing?.customer_reference,
      product_name: existing?.product_name || 'Scenario order', quantity: existing?.quantity || 1,
      priority: item.priority, release_time: new Date(origin + item.release_min * 60_000).toISOString(),
      due_time: new Date(origin + item.due_min * 60_000).toISOString(), status: existing?.status || 'scenario',
      operations: item.operations.map((operation) => ({ id: operation.id, operation_number: operation.operation_number, operation_name: operation.name, options: operation.options })),
    };
  });
}
