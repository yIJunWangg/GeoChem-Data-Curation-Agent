export type Workspace = {
  project_id: string
  project_name: string
  description?: string
  organization_id?: string
  organization_name?: string
  workspace_role?: 'owner' | 'curator' | 'reviewer' | 'viewer' | string
}

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

export type PageSpan = {
  page_number: number
  bbox: number[]
  role?: string
}

export type DocumentElement = {
  element_id: string
  resource_id: string
  element_type: 'table' | 'figure' | 'paragraph'
  page_number: number
  bbox: number[]
  page_spans?: PageSpan[]
  text_content: string
  context_text: string
  caption: string
  preview_path: string
  matched_headers: string[]
  score_reasons?: string[]
  raw_table: {
    headers?: string[]
    body_text?: string
    rows?: string[][]
    source_headers?: string[]
    source_rows?: string[][]
    source_header_rows?: string[][]
    original_headers?: string[]
    header_rows?: string[][]
    header_confidence?: number
    header_parse_method?: string
    standardization_source?: string
    user_edited?: boolean
    edit_reason?: string
    table_standardized?: boolean
    standardization_warnings?: string[]
  }
  relevance_score: number
  status: string
  selected: boolean
  parser_version?: string
  section_path?: string
  source_backend?: string
  merge_reason?: string
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
  applied_rule_id?: string
  extraction_method?: string
  source_label?: string
  source_quote?: string
  source_row_snapshot?: Record<string, unknown>
  evidence_status?: 'complete' | 'weak' | 'insufficient'
  evidence_reason?: string
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
  applied_rules_count?: number
  unmatched_source_fields?: string[]
  rule_memory_snapshot_id?: string
}

export type TableRuleSuggestion = {
  element_id: string
  element_type: string
  table_label: string
  page_number?: number
  source_header: string
  suggested_target_header: string
  confidence: number
  reason: string
  mapping_source?: 'memory' | 'organization_advisory' | 'builtin' | 'exact' | 'knowledge' | 'leaf_exact' | 'similarity' | 'unresolved' | 'llm'
  existing_rule_id?: string
  knowledge_concept_id?: string
  knowledge_concept_version_id?: string
  knowledge_release_id?: string
  chemical_form?: string
  unit_compatibility?: string
  source_references?: string[]
  auto_applied?: boolean
  requires_confirmation?: boolean
  mapping_reason?: string
}

export type TableRulePreflight = {
  article_id: string
  selected_table_count: number
  target_header_count: number
  items: TableRuleSuggestion[]
  llm_assisted_count?: number
  llm_message?: string
  failed_source_headers?: string[]
  rejected_mappings?: { source_header: string; reason: string }[]
  batches?: {
    status: 'success' | 'json_invalid' | 'no_final_output' | 'reasoning_only' | string
    source_headers: string[]
    actual_provider?: string
    actual_model?: string
    provider?: string
    model?: string
    config_version?: string
    retry?: boolean
  }[]
  actual_model?: {
    provider: string
    model: string
    config_version: string
    supports_json_mode?: boolean
    supports_structured_output?: boolean
  }
  config_version?: string
}

export type ParagraphCue = {
  element_id: string
  element_type: 'paragraph'
  page_number?: number
  page_label?: string
  text: string
  caption: string
  matched_headers: string[]
  relevance_score: number
  selected: boolean
  bucket: 'selected' | 'high' | 'possible_missed' | 'low' | 'excluded'
  reason: string
}

export type WorkflowEvent = {
  event_id: number
  level: string
  message: string
  progress: number
  details: Record<string, unknown>
}

export type EvidenceSource = {
  resource_id?: string
  resource_name?: string
  element_id?: string
  element_type?: 'table' | 'figure' | 'paragraph' | string
  page_number?: number
  bbox: number[]
  page_spans?: PageSpan[]
  caption?: string
  context?: string
  target_headers?: string[]
  source_complete?: boolean
}

export type TraceRecordSummary = {
  record_id: string
  article_id: string
  article_title: string
  doi: string
  sample_id: string
  processed_at: string
  nonempty_count: number
  sources: EvidenceSource[]
  source_complete: boolean
}

export type TraceField = {
  target_header: string
  value: string
  target_unit: string
  original_field: string
  original_value: string
  original_unit: string
  review_status: string
  confidence: number
  mapping: { rule_id?: string; mapping_type?: string; formula?: string }
  calculation?: { calc_id?: string; formula?: string; substitution?: string; result?: number; source_unit?: string; target_unit?: string } | null
  source?: EvidenceSource | null
  source_complete: boolean
}

export type TraceRecordDetail = {
  record_id: string
  article_id: string
  article_title: string
  doi: string
  sample_id: string
  processed_at: string
  fields: TraceField[]
  sources: EvidenceSource[]
  resources: Resource[]
}

export type ChatCitation = {
  citation_id?: string
  document_id: string
  label: string
  article_id?: string
  record_id?: string
  cell_id?: string
  element_id?: string
  resource_id?: string
  page_number?: number
  bbox?: number[]
  element_type?: string
  content?: string
  payload?: Record<string, unknown>
}

export type ChatMessage = {
  message_id: string
  thread_id: string
  role: 'user' | 'assistant'
  content: string
  status: string
  agent_run_id?: string
  model_provider?: string
  model_name?: string
  created_at: string
  ui_payload?: Record<string, unknown>
  action_state?: 'pending' | 'selected' | 'superseded' | 'expired' | ''
  superseded_by_message_id?: string
}

export type ChatThread = {
  thread_id: string
  project_id: string
  article_id?: string
  title: string
  scope: 'article' | 'workspace'
  created_at: string
  updated_at: string
  active_run_id?: string
  active_run_status?: 'pending' | 'running' | 'waiting_user' | 'waiting_workbench'
  messages?: ChatMessage[]
  selection_context?: Record<string, unknown>
  latest_actionable_message_id?: string
}

export type AgentActivityEvent = {
  event_id: number | string
  tool_call_id?: string
  event_type?: string
  tool_name?: string
  label?: string
  status?: 'pending' | 'running' | 'completed' | 'failed' | 'rejected' | string
  safe_input_summary?: string | Record<string, unknown>
  safe_output_summary?: string | Record<string, unknown>
  duration_ms?: number
  checkpoint?: string
  error?: string
  level?: string
  message?: string
  created_at?: string
  done?: boolean
  details?: Record<string, unknown>
}

export type AgentRun = {
  run_id: string
  project_id: string
  article_id: string
  thread_id: string
  status: 'pending' | 'running' | 'waiting_user' | 'waiting_workbench' | 'completed' | 'failed' | 'cancelled'
  current_node: string
  pending_interrupt: Record<string, unknown>
  state_summary: Record<string, unknown>
  workflow_step?: string
  checkpoint_kind?: string
  model_provider?: string
  model_name?: string
  handoff_context?: Record<string, unknown>
  workbench_diff?: Record<string, unknown>
  error_message?: string
  activity_events?: AgentActivityEvent[]
}

export type ChatThreadState = {
  thread_id: string
  selection_context: Record<string, unknown>
  active_article_id: string
  active_header_config?: { config_id: string; name: string; field_count: number } | null
  latest_run?: Record<string, unknown> | null
  actual_model: { provider: string; model: string }
  latest_actionable_message_id?: string
}

export type WorkbenchDisplayMode = 'full' | 'embedded'
export type WorkbenchView = 'resources' | 'extract' | 'quality'
export type WorkbenchStage =
  | 'table_standardize'
  | 'mapping'
  | 'tables'
  | 'paragraphs'
  | 'figures'
  | 'edit'
export type ContextPanelMode =
  | 'pdf'
  | 'resource'
  | 'table_standardize'
  | 'mapping'
  | 'candidates'
  | 'quality'
  | 'trace'

export type WorkbenchRuntimeState = {
  project_id: string
  article_id: string
  active_batch_id?: string
  header_config_id?: string
  discovery_status?: string
  view: WorkbenchView
  stage?: WorkbenchStage
}
