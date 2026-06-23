import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import { AllCommunityModule, ModuleRegistry } from 'ag-grid-community'
import 'ag-grid-community/styles/ag-grid.css'
import 'ag-grid-community/styles/ag-theme-quartz.css'
import 'react-pdf/dist/Page/AnnotationLayer.css'
import 'react-pdf/dist/Page/TextLayer.css'
import App from './App'
import './styles.css'

ModuleRegistry.registerModules([AllCommunityModule])
const client = new QueryClient({ defaultOptions: { queries: { staleTime: 15_000, refetchOnWindowFocus: false } } })

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={client}>
      <BrowserRouter><App /></BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
)

