'use client';

import Link from 'next/link';
import Image from 'next/image';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import { Activity, Boxes, CalendarClock, ChevronRight, CircleHelp, ClipboardList, Factory, FileBarChart2, FileInput, Gauge, GitCompareArrows, LayoutDashboard, ListFilter, Menu, Moon, PanelLeftClose, PanelLeftOpen, Play, Search, Settings, Sun } from 'lucide-react';
import { useUi } from '@/stores/ui';
import { useWorkspace } from '../workspace-context';
import { Button, IconButton, Modal } from '../ui';

const groups = [
  { label: 'WORKSPACE', items: [
    { key: 'overview', label: 'Overview', href: '/', icon: LayoutDashboard },
    { key: 'planner', label: 'Production Planner', href: '/planner', icon: ClipboardList },
    { key: 'scheduling', label: 'Scheduling', href: '/scheduling', icon: CalendarClock },
    { key: 'simulation', label: 'Simulation', href: '/simulation', icon: Play },
    { key: 'factory', label: 'Factory Model', href: '/factory', icon: Factory },
  ] },
  { label: 'ANALYSIS', items: [
    { key: 'bottlenecks', label: 'Bottleneck Analysis', href: '/bottlenecks', icon: ListFilter },
    { key: 'utilization', label: 'Resource Utilization', href: '/utilization', icon: Gauge },
    { key: 'scenarios', label: 'Scenario Comparison', href: '/scenarios', icon: GitCompareArrows },
    { key: 'reports', label: 'Reports', href: '/reports', icon: FileBarChart2 },
  ] },
  { label: 'MANAGEMENT', items: [
    { key: 'jobs', label: 'Jobs & Orders', href: '/jobs', icon: Boxes },
    { key: 'machines', label: 'Machines & Work Centers', href: '/machines', icon: Activity },
    { key: 'imports', label: 'Data Imports', href: '/imports', icon: FileInput },
    { key: 'settings', label: 'Settings', href: '/settings', icon: Settings },
  ] },
] as const;

const names: Record<string, string> = { overview: 'Overview', planner: 'Production Planner', jobs: 'Jobs & Orders', scheduling: 'Scheduling Studio', simulation: 'Simulation', factory: 'Factory Model', bottlenecks: 'Bottleneck Analysis', utilization: 'Resource Utilization', scenarios: 'Scenario Lab', reports: 'Reports', machines: 'Machines & Work Centers', imports: 'Data Imports', settings: 'Settings', help: 'Documentation' };

export function AppShell({ module, children }: { module: string; children: React.ReactNode }) {
  const router = useRouter();
  const { bootstrap, facilities, selectFacility, selectedSchedule } = useWorkspace();
  const { theme, setTheme, sidebarCollapsed, toggleSidebar } = useUi();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [showKeys, setShowKeys] = useState(false);
  const [search, setSearch] = useState('');
  const searchRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const keyHandler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setMobileOpen(false); setShowKeys(false); return; }
      const target = event.target as HTMLElement;
      const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(target?.tagName) || target?.isContentEditable;
      if (!typing && event.key === '/') { event.preventDefault(); searchRef.current?.focus(); }
      if (!typing && event.key === '?') { event.preventDefault(); setShowKeys(true); }
    };
    window.addEventListener('keydown', keyHandler);
    return () => window.removeEventListener('keydown', keyHandler);
  }, []);
  const searchJobs = (event: React.FormEvent) => { event.preventDefault(); const query = search.trim(); if (query) { window.dispatchEvent(new CustomEvent('linepilot:job-search', { detail: query })); router.push(`/planner?search=${encodeURIComponent(query)}`); } };
  return <div className="app-shell">
    {mobileOpen && <div className="modal-backdrop" style={{ zIndex: 25 }} onClick={() => setMobileOpen(false)} aria-hidden="true" />}
    <aside className={`sidebar ${sidebarCollapsed ? 'is-collapsed' : ''} ${mobileOpen ? 'is-mobile-open' : ''}`} aria-label="Primary navigation">
      <Link className="sidebar-brand" href="/" onClick={() => setMobileOpen(false)} aria-label="Linepilot overview"><Image className="brand-mark" src="/linepilot-mark.svg" width={31} height={31} alt="" /><span className="brand-copy"><span className="brand-title">LINEPILOT</span><span className="brand-caption">OPERATIONS SYSTEM</span></span></Link>
      <nav className="sidebar-nav">{groups.map((group) => <div className="nav-group" key={group.label}><div className="nav-group-label">{group.label}</div>{group.items.map(({ key, label, href, icon: Icon }) => <Link key={key} href={href} title={label} className={`nav-item ${module === key ? 'active' : ''}`} onClick={() => setMobileOpen(false)}><Icon /><span>{label}</span></Link>)}</div>)}</nav>
      <div className="sidebar-footer"><div className="footer-meta">CURRENT WORKSPACE<strong>{bootstrap.workspace.name}</strong><div style={{ marginTop: 6 }}>{bootstrap.facility.facility_name}{bootstrap.facility.sample_data ? ' · Fictional demo data' : ''}</div></div><button onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')} title="Toggle theme">{theme === 'light' ? <Moon /> : <Sun />}<span>{theme === 'light' ? 'Dark mode' : 'Light mode'}</span></button><button onClick={() => router.push('/help')} title="Help and documentation"><CircleHelp /><span>Help / Documentation</span></button></div>
    </aside>
    <div className="main-frame">
      <header className="topbar"><div className="topbar-left"><IconButton label="Open navigation" className="mobile-menu" onClick={() => setMobileOpen(true)}><Menu /></IconButton><IconButton label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'} onClick={toggleSidebar}>{sidebarCollapsed ? <PanelLeftOpen /> : <PanelLeftClose />}</IconButton><div className="breadcrumb"><span>WORKSPACE</span><ChevronRight size={12} /><strong>{names[module]}</strong></div><div className="topbar-divider" /><div className="topbar-facility"><span>FACILITY</span><select aria-label="Facility" title={bootstrap.facility.facility_name} value={bootstrap.facility.id} onChange={(event) => selectFacility(event.target.value)}>{facilities.map((facility) => <option key={facility.id} value={facility.id}>{facility.facility_code} / {facility.facility_name}</option>)}</select></div></div><div className="topbar-right"><form className="topbar-search" onSubmit={searchJobs}><Search size={14} /><input ref={searchRef} aria-label="Search jobs" placeholder="Search jobs" value={search} onChange={(event) => setSearch(event.target.value)} /><kbd>/</kbd></form><IconButton label="Keyboard shortcuts" onClick={() => setShowKeys(true)}><CircleHelp /></IconButton><div className="menu-button" title="Current schedule status"><span className={`status-dot ${selectedSchedule?.status === 'completed' ? 'success' : 'warning'}`} />{selectedSchedule ? `RUN ${selectedSchedule.id.slice(0, 6).toUpperCase()}` : 'NO SCHEDULE'}</div></div></header>
      <main className="content">{children}</main>
    </div>
    {showKeys && <Modal title="KEYBOARD SHORTCUTS" onClose={() => setShowKeys(false)} footer={<Button onClick={() => setShowKeys(false)}>Close</Button>}><div className="stack"><div><span className="keyboard-hint">/</span> &nbsp; Focus job search</div><div><span className="keyboard-hint">?</span> &nbsp; Show shortcuts</div><div><span className="keyboard-hint">Esc</span> &nbsp; Close dialog or navigation</div><p className="muted tiny">Search opens matching jobs in Production Planner.</p></div></Modal>}
  </div>;
}
