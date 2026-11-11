export type ID = string;

export interface WorkspaceRecord { id: ID; name: string; description?: string; timezone?: string }
export interface Facility { id: ID; facility_code: string; facility_name: string; timezone: string; status?: string; operating_calendar_id?: ID; sample_data?: boolean }
export interface WorkCenter { id: ID; code: string; name: string; display_order?: number; layout_x?: number; layout_y?: number }
export interface OperatingCalendar { id: ID; name: string; timezone: string; weekly_shifts: { day: number; start: string; end: string }[]; holiday_exceptions: string[]; special_closures: { start_time: string; end_time: string }[] }
export interface Downtime { id?: ID; start_time: string; end_time: string; reason?: string; event_type?: string }
export interface Machine {
  id: ID; machine_code: string; machine_name: string; work_center_id: ID;
  hourly_operating_cost: number; status: string; active: boolean; downtime: Downtime[];
  operating_calendar_id?: ID; machine_type?: string;
}
export interface MachineOption { machine_id: ID; processing_minutes: number }
export interface ProductionOperation {
  id: ID; operation_number: number; operation_name: string; default_processing_minutes?: number;
  options: MachineOption[];
}
export interface ProductionJob {
  id: ID; job_number: string; customer_reference?: string; product_name: string; quantity: number;
  priority: number; release_time: string; due_time: string; status: string;
  operations: ProductionOperation[];
}
export interface ScheduledOperation {
  operation_id: ID; job_id: ID; machine_id: ID; start_time: string; end_time: string;
  start_min: number; end_min: number;
}
export interface ScheduleSnapshot {
  origin: string; horizon_minutes: number;
  machines: { id: ID; code: string; work_center_id?: ID; hourly_cost?: number; availability: number[][]; downtime: number[][] }[];
  jobs: { id: ID; job_number: string; release_min: number; due_min: number; priority: number; operations: { id: ID; operation_number: number; name: string; options: MachineOption[] }[] }[];
}
export interface ScheduleMetrics {
  makespan_minutes?: number; makespan?: number; total_tardiness_minutes?: number; total_tardiness?: number;
  total_weighted_tardiness_minutes?: number; weighted_tardiness?: number; on_time_rate?: number;
  on_time_delivery_rate?: number; late_jobs?: number; completed_jobs?: number; scheduled_jobs?: number;
  throughput_jobs_per_hour?: number; throughput?: number; average_waiting_minutes?: number;
  mean_job_waiting_minutes?: number; mean_job_flow_minutes?: number; average_machine_utilization?: number; operating_cost?: number;
  machine_utilization?: Record<ID, number>; utilization?: Record<ID, number>;
  [key: string]: unknown;
}
export interface ScheduleRun {
  id: ID; facility_id: ID; scenario_id?: ID | null; algorithm: string; objective: string;
  name?: string; status: string; solver_status?: string; solver_configuration?: { time_limit_seconds?: number; random_seed?: number; search_workers?: number }; created_at?: string; started_at?: string;
  completed_at?: string; duration_ms?: number; runtime_ms?: number; origin?: string;
  horizon_minutes?: number; input_fingerprint?: string; snapshot?: ScheduleSnapshot; metrics?: ScheduleMetrics; assignments?: ScheduledOperation[];
  diagnostics?: string[] | Record<string, unknown>;
}
export interface ScenarioModification { type: string; machine_id?: ID; start_time?: string; end_time?: string; [key: string]: unknown }
export interface Scenario { id: ID; facility_id: ID; name: string; description?: string; baseline_schedule_id?: ID | null; modifications: ScenarioModification[]; created_at?: string; latest_schedule_id?: ID }
export interface SimulationEvent {
  id?: ID; sequence?: number; time_min?: number; time?: string; simulation_time?: number | string; event_type: string; job_id?: ID; operation_id?: ID;
  machine_id?: ID; payload?: Record<string, unknown>;
}
export interface SimulationRun { id: ID; schedule_run_id: ID; scenario_id?: ID; mode?: string; random_seed?: number; status: string; created_at?: string; configuration?: Record<string, unknown>; metrics?: Record<string, unknown>; summary_metrics?: Record<string, unknown>; events?: SimulationEvent[]; diagnostics?: string[] }
export interface Bootstrap { workspace: WorkspaceRecord; facility: Facility; work_centers: WorkCenter[]; machines: Machine[]; jobs: ProductionJob[]; schedules: ScheduleRun[]; scenarios: Scenario[]; latest_schedule: ScheduleRun | null }
export interface ListResponse<T> { items: T[]; total: number; page: number; page_size: number }
export interface Report { id: ID; schedule_run_id: ID; report_type: string; status: string; created_at?: string }
export interface UtilizationItem { machine_id: ID; machine_code?: string; busy_minutes?: number; available_minutes?: number; idle_minutes?: number; unavailable_minutes?: number; utilization?: number; utilization_rate?: number; downtime_minutes?: number }
export interface BottleneckFinding { machine_id?: ID; machine_code?: string; title?: string; finding?: string; evidence?: string; potential_impact?: string; severity?: string; utilization?: number; queue_waiting_minutes?: number }

export function isFinished(status?: string) { return ['completed', 'failed', 'cancelled'].includes(status || ''); }
export function isSuccess(status?: string) { return ['completed', 'succeeded'].includes(status || ''); }
