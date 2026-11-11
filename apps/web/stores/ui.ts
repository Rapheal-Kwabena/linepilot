import { create } from 'zustand';

type Theme = 'light' | 'dark';
interface UiState {
  theme: Theme;
  selectedFacilityId: string | null;
  facilityPreferenceLoaded: boolean;
  selectedScheduleId: string | null;
  selectedSimulationId: string | null;
  sidebarCollapsed: boolean;
  setTheme: (theme: Theme) => void;
  loadFacilityPreference: () => void;
  setSelectedFacilityId: (id: string | null) => void;
  setSelectedScheduleId: (id: string | null) => void;
  setSelectedSimulationId: (id: string | null) => void;
  toggleSidebar: () => void;
}
export const useUi = create<UiState>((set) => ({
  theme: 'light', selectedFacilityId: null, facilityPreferenceLoaded: false, selectedScheduleId: null, selectedSimulationId: null, sidebarCollapsed: false,
  setTheme: (theme) => { if (typeof window !== 'undefined') { localStorage.setItem('linepilot-theme', theme); document.documentElement.dataset.theme = theme; } set({ theme }); },
  loadFacilityPreference: () => { if (typeof window !== 'undefined') set({ selectedFacilityId: localStorage.getItem('linepilot-facility-id'), facilityPreferenceLoaded: true }); },
  setSelectedFacilityId: (selectedFacilityId) => {
    if (typeof window !== 'undefined') {
      if (selectedFacilityId) localStorage.setItem('linepilot-facility-id', selectedFacilityId);
      else localStorage.removeItem('linepilot-facility-id');
    }
    set({ selectedFacilityId, facilityPreferenceLoaded: true, selectedScheduleId: null, selectedSimulationId: null });
  },
  setSelectedScheduleId: (selectedScheduleId) => set({ selectedScheduleId }),
  setSelectedSimulationId: (selectedSimulationId) => set({ selectedSimulationId }),
  toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
}));
