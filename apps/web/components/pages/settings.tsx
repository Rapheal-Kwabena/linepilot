'use client';

import { Moon, Sun } from 'lucide-react';
import { useWorkspace } from '@/components/workspace-context';
import { useUi } from '@/stores/ui';
import { Button, PageHeader, Panel } from '@/components/ui';

export function SettingsPage() {
  const { bootstrap } = useWorkspace();
  const { theme, setTheme } = useUi();
  return <><PageHeader index="12" title="Settings" subtitle="Workspace context and local display preferences." /><div className="grid-2"><Panel title="Appearance" meta="Saved on this device"><div className="panel-body"><p className="small muted">Choose the interface theme. Charts and engineering controls use the same semantic color system.</p><div className="button-group"><Button className={theme === 'light' ? 'active' : ''} onClick={() => setTheme('light')}><Sun />Industrial light</Button><Button className={theme === 'dark' ? 'active' : ''} onClick={() => setTheme('dark')}><Moon />Industrial dark</Button></div></div></Panel><Panel title="Current workspace" meta={bootstrap.facility.sample_data ? "Fictional demonstration dataset" : "Facility data"}><div className="panel-body"><dl className="detail-list"><dt>Workspace</dt><dd>{bootstrap.workspace.name}</dd><dt>Facility</dt><dd>{bootstrap.facility.facility_name}</dd><dt>Facility code</dt><dd>{bootstrap.facility.facility_code}</dd><dt>Timezone</dt><dd>{bootstrap.facility.timezone}</dd><dt>Work centers</dt><dd>{bootstrap.work_centers.length}</dd><dt>Machines</dt><dd>{bootstrap.machines.length}</dd><dt>Production jobs</dt><dd>{bootstrap.jobs.length}</dd></dl></div></Panel></div><div className="surface-note" style={{ marginTop: 16 }}>{bootstrap.facility.sample_data ? "This facility contains fictional sample data. " : "This facility uses persisted operational data. "}Schedule, simulation and report results are calculated from persisted facility records.</div></>;
}
