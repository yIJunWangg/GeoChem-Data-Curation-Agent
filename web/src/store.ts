import { create } from 'zustand'
import type { CandidateCell, DocumentElement, WorkflowEvent } from './types'

type AppState = {
  projectId: string
  articleId: string
  activeElement?: DocumentElement
  activeCell?: CandidateCell
  logs: WorkflowEvent[]
  setProjectId: (value: string) => void
  setArticleId: (value: string) => void
  setActiveElement: (value?: DocumentElement) => void
  setActiveCell: (value?: CandidateCell) => void
  addLog: (value: WorkflowEvent) => void
  clearLogs: () => void
}

export const useAppStore = create<AppState>((set) => ({
  projectId: '',
  articleId: '',
  logs: [],
  setProjectId: (projectId) => set({ projectId }),
  setArticleId: (articleId) => set({ articleId, activeElement: undefined, activeCell: undefined, logs: [] }),
  setActiveElement: (activeElement) => set({ activeElement }),
  setActiveCell: (activeCell) => set({ activeCell }),
  addLog: (value) => set((state) => ({ logs: [...state.logs.slice(-199), value] })),
  clearLogs: () => set({ logs: [] }),
}))

