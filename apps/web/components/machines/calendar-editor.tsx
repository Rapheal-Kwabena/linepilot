'use client';

import { useEffect, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Plus, Save, Trash2 } from 'lucide-react';
import { api } from '@/lib/api';
import type { OperatingCalendar } from '@/lib/types';
import { Button, Empty, ErrorNotice, Panel } from '@/components/ui';

const weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
type Shift = OperatingCalendar['weekly_shifts'][number];
type Closure = OperatingCalendar['special_closures'][number];

export function CalendarEditor({ calendars, facilityTimezone, facilityCalendarId, onSaved }: { calendars: OperatingCalendar[]; facilityTimezone: string; facilityCalendarId?: string; onSaved: () => Promise<void> }) {
  const [selectedId, setSelectedId] = useState<string>('');
  const [name, setName] = useState('');
  const [timezone, setTimezone] = useState(facilityTimezone);
  const [shifts, setShifts] = useState<Shift[]>([]);
  const [holidaysText, setHolidaysText] = useState('');
  const [closures, setClosures] = useState<Closure[]>([]);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => { if (!selectedId && calendars.length) setSelectedId(calendars.find((calendar) => calendar.id === facilityCalendarId)?.id || calendars[0].id); }, [calendars, selectedId, facilityCalendarId]);
  useEffect(() => {
    const selected = calendars.find((calendar) => calendar.id === selectedId);
    if (!selected) { setName(''); setTimezone(facilityTimezone); setShifts([]); setHolidaysText(''); setClosures([]); return; }
    setName(selected.name); setTimezone(selected.timezone); setShifts(selected.weekly_shifts.map((shift) => ({ ...shift })));
    setHolidaysText(selected.holiday_exceptions.join('\n'));
    setClosures(selected.special_closures.map((closure) => ({ ...closure })));
  }, [selectedId, calendars, facilityTimezone]);
  const save = useMutation({ mutationFn: async () => {
    if (!name.trim()) throw new Error('Calendar name is required.');
    try { new Intl.DateTimeFormat('en-US', { timeZone: timezone }).format(new Date()); } catch { throw new Error('Enter a valid IANA timezone, such as America/Chicago.'); }
    if (shifts.some((shift) => !/^\d{2}:\d{2}$/.test(shift.start) || !/^\d{2}:\d{2}$/.test(shift.end))) throw new Error('Each shift needs start and end times in HH:MM format.');
    const holidays = holidaysText.split(/[\n,]+/).map((item) => item.trim()).filter(Boolean);
    if (holidays.some((item) => !/^\d{4}-\d{2}-\d{2}$/.test(item))) throw new Error('Holiday dates must use YYYY-MM-DD.');
    if (closures.some((item) => !/[zZ]|[+-]\d{2}:\d{2}$/.test(item.start_time) || !/[zZ]|[+-]\d{2}:\d{2}$/.test(item.end_time) || Number.isNaN(Date.parse(item.start_time)) || Number.isNaN(Date.parse(item.end_time)) || Date.parse(item.end_time) <= Date.parse(item.start_time))) throw new Error('Closures need increasing ISO timestamps with a timezone offset.');
    const body = { name: name.trim(), timezone, weekly_shifts: shifts, holiday_exceptions: holidays, special_closures: closures };
    return selectedId === 'new' ? api.createCalendar(body) : api.updateCalendar(selectedId, body);
  }, onSuccess: async (calendar) => { setSelectedId(calendar.id); setError(null); await onSaved(); }, onError: setError });
  const addShift = () => setShifts((current) => [...current, { day: 0, start: '08:00', end: '16:00' }]);
  const updateShift = (index: number, value: Shift) => setShifts((current) => current.map((item, i) => i === index ? value : item));
  const updateClosure = (index: number, value: Closure) => setClosures((current) => current.map((item, i) => i === index ? value : item));
  return <Panel title="Operating calendars" meta="Weekly shifts, holidays and special closures" action={<div className="heading-actions"><select className="control" style={{ width: 220 }} aria-label="Select operating calendar" value={selectedId} onChange={(event) => setSelectedId(event.target.value)}><option value="new">New calendar</option>{calendars.map((calendar) => <option value={calendar.id} key={calendar.id}>{calendar.name}</option>)}</select><Button size="small" onClick={() => setSelectedId('new')}><Plus />New</Button></div>}><div className="panel-body">{error !== null && <ErrorNotice error={error} retry={() => setError(null)} />}<div className="stack"><div className="form-grid"><div className="field"><label htmlFor="calendar-name">Calendar name</label><input id="calendar-name" value={name} onChange={(event) => setName(event.target.value)} /></div><div className="field"><label htmlFor="calendar-timezone">Timezone</label><input id="calendar-timezone" value={timezone} onChange={(event) => setTimezone(event.target.value)} /></div></div>
    <div><div className="panel-header" style={{ paddingLeft: 0, paddingRight: 0 }}><h3>Weekly shifts</h3><Button size="small" onClick={addShift}><Plus />Add shift</Button></div>{shifts.length ? <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Day</th><th>Start</th><th>End</th><th></th></tr></thead><tbody>{shifts.map((shift, index) => <tr key={index}><td><select className="control" value={shift.day} onChange={(event) => updateShift(index, { ...shift, day: Number(event.target.value) })} aria-label={`Shift ${index + 1} day`}>{weekdays.map((day, i) => <option key={day} value={i}>{day}</option>)}</select></td><td><input className="control" type="time" value={shift.start} onChange={(event) => updateShift(index, { ...shift, start: event.target.value })} aria-label={`Shift ${index + 1} start`} /></td><td><input className="control" type="time" value={shift.end} onChange={(event) => updateShift(index, { ...shift, end: event.target.value })} aria-label={`Shift ${index + 1} end`} /></td><td><Button size="small" variant="ghost" onClick={() => setShifts((current) => current.filter((_, i) => i !== index))} aria-label={`Remove shift ${index + 1}`}><Trash2 /></Button></td></tr>)}</tbody></table></div> : <Empty title="NO WEEKLY SHIFTS" text="Add at least one shift for machines using this calendar to be schedulable." />}</div>
    <div className="field"><label htmlFor="calendar-holidays">Holiday exceptions / one YYYY-MM-DD per line</label><textarea id="calendar-holidays" value={holidaysText} onChange={(event) => setHolidaysText(event.target.value)} placeholder="2026-12-25" /></div>
    <div><div className="panel-header" style={{ paddingLeft: 0, paddingRight: 0 }}><h3>Special closures</h3><Button size="small" onClick={() => setClosures((current) => [...current, { start_time: '', end_time: '' }])}><Plus />Add closure</Button></div>{closures.map((closure, index) => <div className="form-grid" key={index} style={{ margin: '10px 0', gridTemplateColumns: '1fr 1fr auto' }}><div className="field"><label htmlFor={`calendar-closure-start-${index}`}>Start / ISO with offset</label><input id={`calendar-closure-start-${index}`} value={closure.start_time} onChange={(event) => updateClosure(index, { ...closure, start_time: event.target.value })} placeholder="2026-11-16T08:00:00-06:00" /></div><div className="field"><label htmlFor={`calendar-closure-end-${index}`}>End / ISO with offset</label><input id={`calendar-closure-end-${index}`} value={closure.end_time} onChange={(event) => updateClosure(index, { ...closure, end_time: event.target.value })} placeholder="2026-11-16T12:00:00-06:00" /></div><Button size="small" variant="ghost" onClick={() => setClosures((current) => current.filter((_, i) => i !== index))} aria-label={`Remove closure ${index + 1}`}><Trash2 /></Button></div>)}</div>
    <div className="surface-note">An end time earlier than or equal to a shift start creates an overnight shift. Updating a calendar changes future scheduling runs; saved run snapshots remain reproducible.</div><div><Button variant="primary" onClick={() => save.mutate()} disabled={save.isPending}><Save />{save.isPending ? 'Saving…' : selectedId === 'new' ? 'Create calendar' : 'Save calendar'}</Button></div>
  </div></div></Panel>;
}
