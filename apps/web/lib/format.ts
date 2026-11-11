export function number(value: unknown, digits = 0): string {
  if (value === null || value === undefined || typeof value !== 'number' || !Number.isFinite(value)) return 'N/A';
  return new Intl.NumberFormat('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
}
export function percent(value: unknown, digits = 1): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'N/A';
  const normalized = Math.abs(value) <= 1 ? value * 100 : value;
  return `${number(normalized, digits)}%`;
}
export function minutes(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'N/A';
  if (value < 60) return `${number(value)} min`;
  return `${number(value / 60, 1)} h`;
}
let displayTimeZone = 'America/Chicago';
export function setDisplayTimeZone(zone: string) { displayTimeZone = zone || 'America/Chicago'; }
export function getDisplayTimeZone() { return displayTimeZone; }
export function dateTime(value?: string, options?: Intl.DateTimeFormatOptions): string {
  if (!value) return 'N/A';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-US', { timeZone: displayTimeZone, ...(options || { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short' }) }).format(date);
}
export function isoToLocalInput(value?: string): string {
  const d = value ? new Date(value) : new Date();
  if (Number.isNaN(d.getTime())) return '';
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: displayTimeZone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(d);
  const part = (name: string) => parts.find((item) => item.type === name)?.value || '00';
  return `${part('year')}-${part('month')}-${part('day')}T${part('hour')}:${part('minute')}`;
}
function zoneParts(date: Date) {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: displayTimeZone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).formatToParts(date);
  const part = (name: string) => Number(parts.find((item) => item.type === name)?.value || 0);
  return { year: part('year'), month: part('month'), day: part('day'), hour: part('hour'), minute: part('minute'), second: part('second') };
}
export function localInputToIso(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value);
  if (!match) throw new Error('Enter a valid facility-local date and time.');
  const [year, month, day, hour, minute] = match.slice(1).map(Number);
  const targetUtc = Date.UTC(year, month - 1, day, hour, minute);
  let estimate = targetUtc;
  for (let i = 0; i < 4; i++) {
    const parts = zoneParts(new Date(estimate));
    const renderedUtc = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute);
    estimate += targetUtc - renderedUtc;
  }
  const result = new Date(estimate);
  const rendered = zoneParts(result);
  if (rendered.year !== year || rendered.month !== month || rendered.day !== day || rendered.hour !== hour || rendered.minute !== minute) throw new Error(`That time does not exist in ${displayTimeZone} because of a clock change.`);
  return result.toISOString();
}
export function statusLabel(value?: string) { return (value || 'unknown').replaceAll('_', ' ').toUpperCase(); }
export function shortId(value?: string) { return value ? value.slice(0, 8).toUpperCase() : 'N/A'; }
export function metric(metrics: Record<string, unknown> | undefined, ...keys: string[]): number | undefined {
  for (const key of keys) { const value = metrics?.[key]; if (typeof value === 'number' && Number.isFinite(value)) return value; }
  return undefined;
}
export function csvEscape(value: unknown) {
  const raw = String(value ?? '');
  const text = typeof value === 'string' && /^[\s]*[=+\-@]/.test(raw) ? `'${raw}` : raw;
  return `"${text.replaceAll('"', '""')}"`;
}
export function csvEncode(value: unknown) { const text = String(value ?? ''); return `"${text.replaceAll('"', '""')}"`; }
