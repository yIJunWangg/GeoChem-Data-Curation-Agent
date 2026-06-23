export type Workspace = { project_id: string; project_name: string }

export type Article = {
  article_id: string
  title: string
  authors?: string
  year?: number
  doi?: string
  url?: string
  journal?: string
  status: string
  resource_count: number
  element_count: number
  header_config_id?: string
}

export type HeaderField = {
  header_id: string
  display_header: string
  canonical_field: string
  target_unit: string
  description: string
  order: number
}

export type HeaderConfig = {
  config_id: string
  name: string
  description: string
  field_count: number
  headers: Record<string, string>[]
}

export type DocumentElement = {
  element_id: string
  resource_id: string
  element_type: 'table' | 'figure' | 'paragraph'
  page_number: number
  bbox: number[]
  text_content: string
  context_text: string
  caption: string
  preview_path: string
  matched_headers: string[]
  raw_table: { headers?: string[]; body_text?: string }
  relevance_score: number
  status: string
  selected: boolean
  parser_version?: string
}

export type Resource = {
  resource_id: string
  resource_type: string
  file_name: string
  status: string
}

export type CandidateCell = {
  cell_id: string
  target_header: string
  target_field: string
  target_unit: string
  value: string
  original_value: string
  original_field: string
  original_unit: string
  confidence: number
  risk_level: string
  mapping_status: string
  review_status: string
  element_id?: string
  page_number?: number
  bbox: number[]
  alternatives: Record<string, unknown>[]
}

export type CandidateRecord = {
  candidate_record_id: string
  sample_id: string
  merge_status: string
  quality_grade: string
  data: Record<string, string>
  cells: Record<string, CandidateCell>
}

export type BatchPayload = {
  batch: { batch_id: string; article_id: string; status: string }
  headers: HeaderField[]
  records: CandidateRecord[]
}

export type WorkflowEvent = {
  event_id: number
  level: string
  message: string
  progress: number
  details: Record<string, unknown>
}

