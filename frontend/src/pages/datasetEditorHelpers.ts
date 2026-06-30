import type { DatasetRow } from "../types";

const SEARCH_LIST_FIELDS = [
  "expected_documents",
  "expected_sections",
  "expected_keywords",
  "alternative_queries"
] as const;

const SEARCH_STRING_FIELDS = [
  "id",
  "vendor",
  "model",
  "scenario_type",
  "topic",
  "difficulty",
  "question",
  "evaluation_focus"
] as const;

const DIFFICULTY_ORDER = ["基础", "中等", "高级"];

export interface DatasetEditorFilters {
  keyword: string;
  scenario: string;
  difficulty: string;
  vendor: string;
  modifiedOnly: boolean;
  errorOnly: boolean;
}

export interface HighlightSegment {
  text: string;
  match: boolean;
}

export interface DatasetSaveConfirmationInput {
  target: "main" | "draft";
  changeCount: number;
  rowCount: number;
  localErrorCount: number;
}

export function toStringList(values: unknown): string[] {
  if (Array.isArray(values)) {
    return values.filter((item): item is string => typeof item === "string");
  }
  if (typeof values === "string" && values.trim()) {
    return values.split(/[,\n]/).map((item) => item.trim()).filter(Boolean);
  }
  return [];
}

function rowSearchText(row: DatasetRow): string {
  const values: string[] = [];
  SEARCH_STRING_FIELDS.forEach((field) => {
    const value = row[field];
    if (typeof value === "string" && value.trim()) values.push(value);
  });
  SEARCH_LIST_FIELDS.forEach((field) => {
    values.push(...toStringList(row[field]));
  });
  return values.join(" ").toLowerCase();
}

export function buildFilterOptions(rows: DatasetRow[]) {
  const vendors = new Set<string>();
  const scenarios = new Set<string>();
  const difficulties = new Set<string>();
  rows.forEach((row) => {
    if (typeof row.vendor === "string" && row.vendor.trim()) vendors.add(row.vendor);
    if (typeof row.scenario_type === "string" && row.scenario_type.trim()) scenarios.add(row.scenario_type);
    if (typeof row.difficulty === "string" && row.difficulty.trim()) difficulties.add(row.difficulty);
  });
  return {
    vendors: Array.from(vendors).sort((a, b) => a.localeCompare(b, "zh-Hans-CN")),
    scenarios: Array.from(scenarios).sort((a, b) => a.localeCompare(b, "zh-Hans-CN")),
    difficulties: Array.from(difficulties).sort((a, b) => {
      const ai = DIFFICULTY_ORDER.indexOf(a);
      const bi = DIFFICULTY_ORDER.indexOf(b);
      if (ai !== -1 || bi !== -1) {
        if (ai === -1) return 1;
        if (bi === -1) return -1;
        return ai - bi;
      }
      return a.localeCompare(b, "zh-Hans-CN");
    })
  };
}

export function rowMatchesFilters(
  row: DatasetRow,
  index: number,
  filters: DatasetEditorFilters,
  modifiedRows: Set<number>,
  rowErrorCounts: Map<number, number>
): boolean {
  if (filters.scenario && row.scenario_type !== filters.scenario) return false;
  if (filters.difficulty && row.difficulty !== filters.difficulty) return false;
  if (filters.vendor && row.vendor !== filters.vendor) return false;
  if (filters.modifiedOnly && !modifiedRows.has(index)) return false;
  if (filters.errorOnly && (rowErrorCounts.get(index) || 0) === 0) return false;
  const keyword = filters.keyword.trim().toLowerCase();
  if (keyword && !rowSearchText(row).includes(keyword)) return false;
  return true;
}

export function highlightSegments(text: string, keyword: string): HighlightSegment[] {
  const needle = keyword.trim();
  if (!needle) return [{ text, match: false }];
  const lowerText = text.toLowerCase();
  const lowerNeedle = needle.toLowerCase();
  const segments: HighlightSegment[] = [];
  let cursor = 0;
  while (cursor < text.length) {
    const index = lowerText.indexOf(lowerNeedle, cursor);
    if (index === -1) {
      segments.push({ text: text.slice(cursor), match: false });
      break;
    }
    if (index > cursor) segments.push({ text: text.slice(cursor, index), match: false });
    segments.push({ text: text.slice(index, index + needle.length), match: true });
    cursor = index + needle.length;
  }
  return segments.filter((segment) => segment.text.length > 0);
}

export function nextActiveIndexAfterDelete(deletedIndex: number, rowCountBeforeDelete: number): number | null {
  if (rowCountBeforeDelete <= 1) return null;
  if (deletedIndex < rowCountBeforeDelete - 1) return deletedIndex;
  return rowCountBeforeDelete - 2;
}

export function pageForIndex(index: number, pageSize: number): number {
  return Math.floor(index / pageSize) + 1;
}

export function buildDatasetSaveConfirmation({
  target,
  changeCount,
  rowCount,
  localErrorCount
}: DatasetSaveConfirmationInput): string {
  if (target === "draft") {
    return `确认把当前 ${changeCount} / ${rowCount} 行的修改保存到草稿？\n${
      localErrorCount > 0 ? `（注意：仍有 ${localErrorCount} 条本地校验告警，服务端会再次校验。）` : ""
    }`;
  }
  if (localErrorCount > 0) {
    return `确认把当前 ${changeCount} / ${rowCount} 行的修改直接覆盖到主评测集？\n（注意：仍有 ${localErrorCount} 条本地校验告警，服务端会再次校验。）`;
  }
  return `确认把当前 ${changeCount} / ${rowCount} 行的修改直接覆盖到主评测集？此操作会覆盖原文件。`;
}

export function buildDatasetDiscardConfirmation(changeCount: number): string {
  if (changeCount > 0) {
    return `确认放弃当前 ${changeCount} 行的本地修改，并重新加载评测集？未保存的改动会丢失。`;
  }
  return "确认重新加载评测集吗？";
}
