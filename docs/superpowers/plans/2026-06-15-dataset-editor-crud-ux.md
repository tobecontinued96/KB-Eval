# Dataset Editor CRUD UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the dataset editor table and CRUD workflow with compact rows, composite search filters, search highlighting, safer row focus after add/delete, and editable row details.

**Architecture:** Keep `DatasetEditor` as the page container, add a small pure helper module for filter/highlight/focus logic, and upgrade the existing row detail panel into an editor for long fields and list fields. The implementation keeps current JSONL save/review APIs unchanged.

**Tech Stack:** React 19, TypeScript, Vite, lucide-react, Node built-in `node:test` for helper tests, existing CSS.

---

## File Structure

- Create `frontend/src/pages/datasetEditorHelpers.ts`: pure helpers for string list normalization, filter options, row matching, highlight segments, delete focus, and page calculation.
- Create `frontend/src/pages/datasetEditorHelpers.test.mjs`: Node test runner tests that import the compiled helper module from `frontend/.tmp/test-build/datasetEditorHelpers.js`.
- Modify `frontend/package.json`: add `test:helpers` script that compiles the helper module to `.tmp/test-build` and runs the Node tests.
- Modify `frontend/src/pages/DatasetEditor.tsx`: wire new filter state, compact row rendering, add/delete focus behavior, local/server error maps, keyword highlighting, and editable detail panel.
- Modify `frontend/src/styles.css`: compact table rows, toolbar filter layout, search highlights, row status chips, and detail editor controls.

### Task 1: Helper Test Harness

**Files:**
- Create: `frontend/src/pages/datasetEditorHelpers.test.mjs`
- Modify: `frontend/package.json`

- [ ] **Step 1: Write the failing helper tests**

Create `frontend/src/pages/datasetEditorHelpers.test.mjs`:

```javascript
import test from "node:test";
import assert from "node:assert/strict";
import {
  buildFilterOptions,
  highlightSegments,
  nextActiveIndexAfterDelete,
  pageForIndex,
  rowMatchesFilters,
  toStringList
} from "../../.tmp/test-build/datasetEditorHelpers.js";

const row = {
  id: "HW-S1720-EV-001",
  vendor: "华为",
  model: "S1720",
  scenario_type: "故障恢复",
  topic: "关于本章",
  difficulty: "基础",
  question: "华为 S1720 遇到关于本章相关问题时应该如何处理？",
  evaluation_focus: "应命中 MinERU_markdown_01-01 常见系统操作章节",
  expected_documents: ["MinERU_markdown_01-01.pdf"],
  expected_sections: ["关于本章"],
  expected_keywords: ["本章", "告警"],
  alternative_queries: ["如何处理本章问题"]
};

test("toStringList normalizes arrays and delimited strings", () => {
  assert.deepEqual(toStringList(["a", 1, "b"]), ["a", "b"]);
  assert.deepEqual(toStringList("a, b\nc"), ["a", "b", "c"]);
  assert.deepEqual(toStringList(null), []);
});

test("buildFilterOptions returns sorted unique vendor scenario and difficulty options", () => {
  const options = buildFilterOptions([
    row,
    { ...row, vendor: "中兴", scenario_type: "安装部署", difficulty: "高级" },
    { ...row, vendor: "华为", scenario_type: "故障恢复", difficulty: "基础" }
  ]);

  assert.deepEqual(options.vendors, ["中兴", "华为"]);
  assert.deepEqual(options.scenarios, ["安装部署", "故障恢复"]);
  assert.deepEqual(options.difficulties, ["基础", "高级"]);
});

test("rowMatchesFilters combines keyword dropdown toggles modified and errors", () => {
  const modifiedRows = new Set([0]);
  const errorCounts = new Map([[0, 2]]);

  assert.equal(rowMatchesFilters(row, 0, {
    keyword: "本章",
    scenario: "故障恢复",
    difficulty: "基础",
    vendor: "华为",
    modifiedOnly: true,
    errorOnly: true
  }, modifiedRows, errorCounts), true);

  assert.equal(rowMatchesFilters(row, 0, {
    keyword: "不存在",
    scenario: "故障恢复",
    difficulty: "基础",
    vendor: "华为",
    modifiedOnly: true,
    errorOnly: true
  }, modifiedRows, errorCounts), false);

  assert.equal(rowMatchesFilters(row, 1, {
    keyword: "本章",
    scenario: "",
    difficulty: "",
    vendor: "",
    modifiedOnly: true,
    errorOnly: false
  }, modifiedRows, errorCounts), false);
});

test("highlightSegments marks case insensitive matches without dropping text", () => {
  assert.deepEqual(highlightSegments("S1720 本章 S1720", "s1720"), [
    { text: "S1720", match: true },
    { text: " 本章 ", match: false },
    { text: "S1720", match: true }
  ]);
  assert.deepEqual(highlightSegments("无关键词", ""), [{ text: "无关键词", match: false }]);
});

test("nextActiveIndexAfterDelete keeps row detail anchored to a neighbor", () => {
  assert.equal(nextActiveIndexAfterDelete(0, 1), null);
  assert.equal(nextActiveIndexAfterDelete(0, 3), 0);
  assert.equal(nextActiveIndexAfterDelete(2, 3), 1);
});

test("pageForIndex returns one based page numbers", () => {
  assert.equal(pageForIndex(0, 20), 1);
  assert.equal(pageForIndex(20, 20), 2);
  assert.equal(pageForIndex(79, 20), 4);
});
```

Add this script to `frontend/package.json`:

```json
"test:helpers": "tsc src/pages/datasetEditorHelpers.ts --target ES2022 --module ES2022 --moduleResolution Bundler --outDir .tmp/test-build --skipLibCheck --strict --esModuleInterop --allowSyntheticDefaultImports && node src/pages/datasetEditorHelpers.test.mjs"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm run test:helpers` from `frontend`.

Expected: FAIL because `src/pages/datasetEditorHelpers.ts` does not exist.

### Task 2: Pure Helper Implementation

**Files:**
- Create: `frontend/src/pages/datasetEditorHelpers.ts`
- Test: `frontend/src/pages/datasetEditorHelpers.test.mjs`

- [ ] **Step 1: Implement the helper module**

Create `frontend/src/pages/datasetEditorHelpers.ts`:

```typescript
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
    difficulties: Array.from(difficulties).sort((a, b) => a.localeCompare(b, "zh-Hans-CN"))
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
```

- [ ] **Step 2: Run helper tests**

Run: `npm run test:helpers` from `frontend`.

Expected: PASS for all 6 tests.

### Task 3: Wire Composite Filters And Add/Delete Focus

**Files:**
- Modify: `frontend/src/pages/DatasetEditor.tsx`
- Test: `frontend/src/pages/datasetEditorHelpers.test.mjs`

- [ ] **Step 1: Import helpers and add filter states**

Update imports in `DatasetEditor.tsx` to include:

```typescript
import {
  buildFilterOptions,
  highlightSegments,
  nextActiveIndexAfterDelete,
  pageForIndex,
  rowMatchesFilters,
  toStringList,
  type DatasetEditorFilters
} from "./datasetEditorHelpers";
```

Replace local `stringList` usage with `toStringList`, and add states:

```typescript
const [difficultyFilter, setDifficultyFilter] = useState("");
const [vendorFilter, setVendorFilter] = useState("");
const [modifiedOnly, setModifiedOnly] = useState(false);
const [errorOnly, setErrorOnly] = useState(false);
```

- [ ] **Step 2: Replace visible row filtering**

Compute modified rows before `visibleRows`, derive row error counts from local and server errors, then filter with `rowMatchesFilters`.

- [ ] **Step 3: Update add/delete behavior**

`addRow` should clear filters, append a new row without appending to `baselineRows`, activate the new row, and call `setPage(pageForIndex(nextIndex, pageSize))`.

`removeRow` should use `nextActiveIndexAfterDelete(index, rows.length)`, update rows and baseline rows, activate the neighbor, and clamp page to the final valid page.

- [ ] **Step 4: Run helper tests and build**

Run from `frontend`:

```powershell
npm run test:helpers
npm run build
```

Expected: helper tests pass and build exits 0.

### Task 4: Compact Table And Highlighted Previews

**Files:**
- Modify: `frontend/src/pages/DatasetEditor.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Add highlighted preview rendering**

Add a `HighlightedText` component that maps `highlightSegments(text, keyword)` to text nodes and `<mark className="search-hit">`.

Pass `filter` to `EditorRow`, `LongTextCell`, `CompactTagList`, and `EditableChip` for preview highlighting.

- [ ] **Step 2: Make table rows compact**

Update `styles.css` so browse rows have stable height, long cells clamp to 2 lines, tag lists stay on one line in table cells, and `+N` remains visible.

- [ ] **Step 3: Run build**

Run: `npm run build` from `frontend`.

Expected: build exits 0.

### Task 5: Editable Row Detail Panel

**Files:**
- Modify: `frontend/src/pages/DatasetEditor.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Extend `RowDetailDrawer` props**

Pass `modified`, `errors`, `onAddList`, `onRemoveList`, and `onEditList` from the parent. The detail panel should not call save APIs directly.

- [ ] **Step 2: Replace read-only field rows with inputs**

Render short metadata fields as inputs/selects, long fields as textareas, list fields with `CompactTagList`, and metadata passthrough as the existing JSON textarea.

- [ ] **Step 3: Add row status summary**

Show modified and error summary chips at the top of the detail panel.

- [ ] **Step 4: Run build**

Run: `npm run build` from `frontend`.

Expected: build exits 0.

### Task 6: Final Verification

**Files:**
- Verify: `frontend/src/pages/DatasetEditor.tsx`
- Verify: `frontend/src/styles.css`
- Verify: `frontend/src/pages/datasetEditorHelpers.ts`
- Verify: `frontend/src/pages/datasetEditorHelpers.test.mjs`

- [ ] **Step 1: Run automated checks**

Run from `frontend`:

```powershell
npm run test:helpers
npm run build
```

Expected: both commands exit 0.

- [ ] **Step 2: Start the dev server**

Run from `frontend`:

```powershell
npm run dev -- --host 127.0.0.1
```

Expected: Vite reports a local URL.

- [ ] **Step 3: Browser smoke test**

Open the dataset editor page in the in-app browser when the route is known, or open the app root and navigate to the editor. Verify:

- rows are compact;
- keyword/filter controls are visible;
- adding a row activates it;
- detail fields are editable;
- no obvious text overlap at desktop width.

- [ ] **Step 4: Report residual risks**

If browser smoke cannot reach live data, report that automated checks passed and note the browser/data limitation.
