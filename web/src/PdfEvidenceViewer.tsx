import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import pdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import {
  ChevronDown, ChevronLeft, ChevronRight, ChevronsUpDown, Focus,
  Maximize2, Minus, Plus,
} from 'lucide-react'
import { api, authenticatedFile } from './api'
import type { EvidenceSource, Resource } from './types'

pdfjs.GlobalWorkerOptions.workerSrc = pdfWorker

export type PdfEvidence = EvidenceSource & {
  target_header?: string
  value?: string
  target_unit?: string
  original_field?: string
  original_value?: string
  original_unit?: string
  confidence?: number
  review_status?: string
}

type Props = {
  projectId: string
  resources: Resource[]
  articleScopeKey?: string
  evidence?: PdfEvidence | null
  details?: ReactNode
  onRequestFocus?: () => void
}

const clamp = (value: number, minimum: number, maximum: number) => Math.max(minimum, Math.min(maximum, value))

export function PdfEvidenceViewer({ projectId, resources, articleScopeKey = '', evidence, details, onRequestFocus }: Props) {
  const pdfResources = useMemo(() => resources.filter((resource) => resource.resource_type.includes('pdf')), [resources])
  const [resourceId, setResourceId] = useState('')
  const [pageNumber, setPageNumber] = useState(1)
  const [pageCount, setPageCount] = useState(0)
  const [zoom, setZoom] = useState(1)
  const [fitWidth, setFitWidth] = useState(true)
  const [containerWidth, setContainerWidth] = useState(430)
  const [detailsOpen, setDetailsOpen] = useState(true)
  const [activeSpanIndex, setActiveSpanIndex] = useState(0)
  const containerRef = useRef<HTMLDivElement>(null)
  const evidenceRef = useRef<HTMLDivElement>(null)

  const pdfResourceIds = useMemo(() => new Set(pdfResources.map((resource) => resource.resource_id)), [pdfResources])

  useEffect(() => {
    const evidenceResourceId = evidence?.resource_id && pdfResourceIds.has(evidence.resource_id)
      ? evidence.resource_id
      : ''
    const firstSpan = evidenceResourceId ? evidence?.page_spans?.[0] : undefined
    setResourceId(evidenceResourceId || pdfResources[0]?.resource_id || '')
    setPageNumber(firstSpan?.page_number || (evidenceResourceId ? evidence?.page_number : undefined) || 1)
    setPageCount(0)
    setZoom(1)
    setFitWidth(true)
    setActiveSpanIndex(0)
  }, [articleScopeKey])

  useEffect(() => {
    if (!pdfResources.length) {
      if (resourceId) setResourceId('')
      return
    }
    if (!resourceId || !pdfResourceIds.has(resourceId)) {
      setResourceId(pdfResources[0].resource_id)
      setPageNumber(1)
      setPageCount(0)
    }
  }, [pdfResourceIds, pdfResources, resourceId])

  useEffect(() => {
    if (evidence?.resource_id && pdfResourceIds.has(evidence.resource_id)) setResourceId(evidence.resource_id)
    const firstSpan = evidence?.page_spans?.[0]
    if (firstSpan?.page_number) {
      setActiveSpanIndex(0)
      setPageNumber(firstSpan.page_number)
    } else if (evidence?.page_number) {
      setActiveSpanIndex(0)
      setPageNumber(evidence.page_number)
    }
  }, [evidence?.resource_id, evidence?.page_number, evidence?.page_spans, pdfResourceIds])

  useEffect(() => {
    if (!containerRef.current) return
    const observer = new ResizeObserver(([entry]) => setContainerWidth(entry.contentRect.width))
    observer.observe(containerRef.current)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => evidenceRef.current?.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' }), 180)
    return () => window.clearTimeout(timer)
  }, [resourceId, pageNumber, evidence?.bbox?.join(','), evidence?.page_spans?.map((span) => `${span.page_number}:${span.bbox.join(',')}`).join('|')])

  const changePage = (next: number) => setPageNumber(clamp(Math.round(next || 1), 1, pageCount || 1))
  const changeZoom = (delta: number) => {
    setFitWidth(false)
    setZoom((current) => clamp(Math.round((current + delta) * 10) / 10, 0.5, 2.5))
  }
  const renderedWidth = fitWidth ? Math.max(320, containerWidth - 28) : Math.round(720 * zoom)
  const pdfFile = useMemo(() => resourceId && pdfResourceIds.has(resourceId) ? authenticatedFile(api.pdfUrl(projectId, resourceId)) : undefined, [pdfResourceIds, projectId, resourceId])
  const spans = evidence?.page_spans?.length ? evidence.page_spans : (evidence?.bbox?.length === 4 && evidence?.page_number ? [{ page_number: evidence.page_number, bbox: evidence.bbox, role: 'evidence' }] : [])
  const activeSpan = spans[Math.min(activeSpanIndex, Math.max(0, spans.length - 1))]
  const bbox = activeSpan?.bbox || []
  const showEvidence = bbox.length === 4 && activeSpan?.page_number === pageNumber && (!evidence?.resource_id || evidence.resource_id === resourceId)

  return <section className="pdf-evidence-viewer">
    <div className="pdf-evidence-toolbar">
      <select aria-label="PDF 资源" value={resourceId} onChange={(event) => { setResourceId(event.target.value); setPageNumber(1); setPageCount(0) }}>
        {pdfResources.map((resource) => <option key={resource.resource_id} value={resource.resource_id}>{resource.file_name}</option>)}
      </select>
      <button className="icon-button" title="上一页" disabled={pageNumber <= 1} onClick={() => changePage(pageNumber - 1)}><ChevronLeft size={16}/></button>
      <label className="pdf-page-input"><input aria-label="PDF 页码" type="number" min={1} max={pageCount || 1} value={pageNumber} onChange={(event) => changePage(Number(event.target.value))}/><span>/ {pageCount || '?'}</span></label>
      <button className="icon-button" title="下一页" disabled={!pageCount || pageNumber >= pageCount} onClick={() => changePage(pageNumber + 1)}><ChevronRight size={16}/></button>
      <span className="pdf-toolbar-divider" />
      {spans.length > 1 && <><button disabled={activeSpanIndex <= 0} onClick={() => { const next = Math.max(0, activeSpanIndex - 1); setActiveSpanIndex(next); setPageNumber(spans[next].page_number) }}>证据 {activeSpanIndex + 1}/{spans.length}</button><button disabled={activeSpanIndex >= spans.length - 1} onClick={() => { const next = Math.min(spans.length - 1, activeSpanIndex + 1); setActiveSpanIndex(next); setPageNumber(spans[next].page_number) }}>下一处</button><span className="pdf-toolbar-divider" /></>}
      <button className="icon-button" title="缩小" onClick={() => changeZoom(-0.1)}><Minus size={15}/></button>
      <button title="恢复 100%" onClick={() => { setFitWidth(false); setZoom(1) }}>{fitWidth ? '适宽' : `${Math.round(zoom * 100)}%`}</button>
      <button className="icon-button" title="放大" onClick={() => changeZoom(0.1)}><Plus size={15}/></button>
      <button className="icon-button" title="适应宽度" onClick={() => setFitWidth(true)}><ChevronsUpDown size={15}/></button>
      {onRequestFocus && <button className="icon-button" title="聚焦 PDF" onClick={onRequestFocus}><Maximize2 size={15}/></button>}
    </div>
    <div className="pdf-evidence-scroll" ref={containerRef}>
      {pdfFile ? <Document
        key={`${articleScopeKey}:${resourceId}`}
        file={pdfFile}
        onLoadSuccess={({ numPages }) => { setPageCount(numPages); setPageNumber((current) => clamp(current, 1, numPages)) }}
        loading={<div className="empty-state compact">正在载入 PDF...</div>}
        error={<div className="empty-state compact">PDF 加载失败，请检查原文件。</div>}
      >
        <div className="pdf-page-wrap evidence-page-wrap">
          <Page pageNumber={pageNumber} width={renderedWidth} renderAnnotationLayer={false} renderTextLayer />
          {showEvidence && <div
            ref={evidenceRef}
            className={`evidence-box-wrap active ${evidence?.element_type || 'paragraph'}`}
            title={evidence?.target_header || evidence?.caption || '来源证据'}
            style={{ left: `${bbox[0] * 100}%`, top: `${bbox[1] * 100}%`, width: `${(bbox[2] - bbox[0]) * 100}%`, height: `${(bbox[3] - bbox[1]) * 100}%` }}
          />}
        </div>
      </Document> : <div className="empty-state compact"><Focus size={26}/><span>当前文章没有可用 PDF。</span></div>}
    </div>
    {(evidence || details) && <div className={`pdf-evidence-details ${detailsOpen ? 'open' : ''}`}>
      <button className="pdf-details-toggle" onClick={() => setDetailsOpen((open) => !open)}><strong>证据详情</strong><ChevronDown size={15}/></button>
      {detailsOpen && (details || <dl>
        <dt>目标字段</dt><dd>{evidence?.target_header || '—'}</dd>
        <dt>当前值</dt><dd>{evidence?.value || '—'} {evidence?.target_unit || ''}</dd>
        <dt>原始字段</dt><dd>{evidence?.original_field || '—'}</dd>
        <dt>原始值</dt><dd>{evidence?.original_value || '—'} {evidence?.original_unit || ''}</dd>
        <dt>置信度</dt><dd>{evidence?.confidence == null ? '—' : `${Math.round(evidence.confidence * 100)}%`}</dd>
        <dt>审核状态</dt><dd>{evidence?.review_status || '—'}</dd>
      </dl>)}
    </div>}
  </section>
}
