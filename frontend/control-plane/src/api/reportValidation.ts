import type { HistoryResponse, PublicRunReport, ReportExportResponse, ReportFact } from './types';

const object = (v: unknown): v is ReportFact => !!v && typeof v === 'object' && !Array.isArray(v);
const text = (v: unknown): v is string => typeof v === 'string';
const number = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v >= 0;
const integer = (v: unknown) => number(v) && Number.isInteger(v);
const optional = (v: unknown, predicate: (v: unknown) => boolean) => v === undefined || v === null || predicate(v);
const strings = (v: unknown) => Array.isArray(v) && v.every(text);
const date = (v: unknown) => text(v) && Number.isFinite(Date.parse(v));
const platform = (v: unknown) => ['web','android','windows','macos'].includes(String(v));
const statuses = new Set(['success','passed','completed','failed','error','inconclusive','cancelled','interrupted','incomplete','preparing','running','finalizing','pending','skipped','unknown','not_requested','not_applicable','complete','partial','unavailable','available','missing','omitted','unsupported','unchanged','changed','comparable','unmatched','matched']);
const state = (v: unknown) => text(v) && statuses.has(v);
const json = (v: unknown, depth = 0): boolean => depth < 32 && (v === null || text(v) || typeof v === 'boolean' || typeof v === 'number' && Number.isFinite(v) || Array.isArray(v) && v.every(item => json(item, depth + 1)) || object(v) && Object.values(v).every(item => json(item, depth + 1)));
const stringKeys = ['run_id','artifact_id','step_id','step_execution_id','source_step_id','tool_call_id','case_id','case_path','goal_summary','action_name','authored_action_name','capability_name','kind','phase','lifecycle_phase','tool_origin','tool_name','message','summary','error_message','failure_category','skip_reason','blocked_by_step','capture_reason','reason','unavailable_reason','content','normalized_content','path','mime_type','sha256','digest','label','unit','scope'];
const boolKeys = ['truncated','transformed','redacted','display_transformed','container','attempted','trustworthy'];
const numKeys = ['duration_ms','size_bytes','attempt_index','max_attempts','capture_occurrence','sequence','before_number','after_number'];
export function reportFact(value: unknown): value is ReportFact {
  if (!object(value) || !json(value)) return false;
  if (!stringKeys.every(key => optional(value[key], text)) || !boolKeys.every(key => optional(value[key], v => typeof v === 'boolean')) || !numKeys.every(key => optional(value[key], integer))) return false;
  if (!['started_at','ended_at','completed_at','timestamp','time'].every(key => optional(value[key], date))) return false;
  if (!optional(value.invocation_path, v => strings(v) || text(v)) || !optional(value.status,state) || !optional(value.outcome,state)) return false;
  if (value.metrics !== undefined && !metrics(value.metrics)) return false;
  if (!optional(value.complete_artifact, v => object(v) && text(v.run_id) && text(v.artifact_id))) return false;
  if (!optional(value.display_availability,text)) return false;
  if (!['coverage','compaction'].every(key => optional(value[key],object))) return false;
  return true;
}
export function metrics(value: unknown): boolean {
  if (!object(value)) return false;
  return Object.values(value).every(item => !object(item) || !('value' in item) || optional(item.value,number) && optional(item.unit,text) && optional(item.scope,text) && optional(item.availability,text) && optional(item.unavailable_reason,text));
}
export function diff(value: unknown): boolean {
  if (!reportFact(value) || !Array.isArray(value.rows)) return false;
  return value.rows.every(row => object(row) && ['context','changed','removed','added'].includes(String(row.kind)) && optional(row.before,text) && optional(row.after,text) && optional(row.before_number,integer) && optional(row.after_number,integer) && ['before_segments','after_segments'].every(key => optional(row[key], v => Array.isArray(v) && v.every(s => object(s) && text(s.text) && typeof s.changed === 'boolean'))));
}
export function historyValid(value: unknown): value is HistoryResponse {
  if (!object(value) || !text(value.workspace) || !Array.isArray(value.platforms) || !value.platforms.every(platform) || !object(value.filters) || !integer(value.matched_count) || !integer(value.returned_count) || typeof value.truncated !== 'boolean' || !strings(value.warnings) || !Array.isArray(value.runs)) return false;
  return value.returned_count === value.runs.length && Number(value.matched_count) >= Number(value.returned_count) && value.runs.every(item => reportFact(item) && text(item.run_id) && platform(item.platform) && optional(item.status,state) && optional(item.mode,v => ['strict','explore'].includes(String(v))) && optional(item.source,reportFact) && optional(item.result,reportFact) && optional(item.evidence,reportFact) && strings(item.warnings) && optional(item.liveness,v => ['alive','dead','unknown'].includes(String(v))) && optional(item.persisted_status,state));
}
export function publicReportValid(value: unknown): value is PublicRunReport {
  if (!object(value) || value.schema_version !== 'fsq.report/v1' || !['run','source','execution','verification','evidence','processing','metrics','lineage','comparison'].every(key => object(value[key])) || !['steps','tool_calls','logs','artifacts'].every(key => Array.isArray(value[key]) && (value[key] as unknown[]).every(reportFact)) || !strings(value.warnings)) return false;
  for (const key of ['run','source','execution','verification','evidence']) if (!reportFact(value[key])) return false;
  if (!optional((value.execution as ReportFact).interpretations, v => Array.isArray(v) && v.every(item => object(item) && ['recorded_fact','deterministic_classification','ai_suggestion'].includes(String(item.kind)) && optional(item.label,text) && optional(item.text,text) && optional(item.unavailable_reason,text) && Array.isArray(item.references) && item.references.every(ref => object(ref) && text(ref.run_id) && optional(ref.artifact_id,text) && optional(ref.step_execution_id,text))))) return false;
  const run = value.run as ReportFact;
  if (!text(run.run_id) || !platform(run.platform) || !optional(run.mode,v => ['strict','explore'].includes(String(v))) || !object(run.gate) || !['passed','failed','error','incomplete'].includes(String(run.gate.status)) || !Array.isArray(run.gate.reasons) || !run.gate.reasons.every(item => text(item) || reportFact(item))) return false;
  if (!metrics(value.metrics) || !Object.values(value.processing as ReportFact).every(item => reportFact(item))) return false;
  const comparison = value.comparison as ReportFact;
  if (!optional(comparison.before_after,v => Array.isArray(v) && v.every(diff))) return false;
  if (comparison.baseline_current != null) {
    const baseline = comparison.baseline_current;
    if (!reportFact(baseline) || !optional(baseline.baseline_run_id,text) || !optional(baseline.current_run_id,text) || !optional(baseline.steps,v => Array.isArray(v) && v.every(item => object(item) && optional(item.current,reportFact) && optional(item.baseline,reportFact) && optional(item.snapshot,diff)))) return false;
  }
  if (!optional(comparison.baseline_report, publicReportValid)) return false;
  return optional((value.lineage as ReportFact).related_runs,v => Array.isArray(v) && v.every(item => object(item) && (item.schema_version === 'fsq.report/v1' ? publicReportValid(item) : reportFact(item) && text(item.run_id) && optional(item.execution,reportFact))));
}
export function exportValid(value: unknown): value is ReportExportResponse {
  return object(value) && text(value.run_id) && platform(value.platform) && text(value.export_id) && ['json','junit','html','bundle'].includes(String(value.format)) && Array.isArray(value.files) && value.files.length > 0 && value.files.every(item => object(item) && text(item.file_id) && text(item.name) && text(item.mime_type) && integer(item.size_bytes) && optional(item.sha256,text) && text(item.download_url)) && strings(value.warnings);
}
