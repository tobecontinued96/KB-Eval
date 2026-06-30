# Dataset Manual Review Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce that generated or legacy evaluation datasets must be manually reviewed before any evaluation run can be created.

**Architecture:** The backend owns the invariant: `RunService.create_run()` rejects any dataset whose review state is not `reviewed`. The frontend mirrors the rule by disabling the run form for `draft` and `unreviewed` datasets and by guiding users to the existing dataset editor review action.

**Tech Stack:** Python 3.12, FastAPI service layer, `unittest`, React 19, TypeScript, Vite.

---

## File Structure

- Create `tests/test_dataset_review_gate.py`: backend unit tests for canonical draft paths, editable path resolution, dataset listing, and run creation gating.
- Modify `backend/services/dataset_edit_service.py`: allow the editor to open the canonical main JSONL path when only `<stem>.draft.jsonl` exists.
- Modify `backend/services/run_service.py`: list draft-only datasets under their canonical main path and reject non-reviewed datasets in `create_run()`.
- Modify `frontend/src/api.ts`: preserve backend error `code` and `detail` so stale UI states still show useful failures.
- Modify `frontend/src/pages/Home.tsx`: remove the confirm override, disable run creation for non-reviewed datasets, and add an audit entry link.
- Modify `frontend/src/pages/Datasets.tsx`: after generation, route the main action to the editor review step instead of the run form.
- Modify `frontend/src/styles.css`: add small review-gate affordance styles if existing `warning-line` and button styles are not enough.
- Modify `README.md` and `docs/API契约.md`: document the hard review gate.

---

### Task 1: Backend Tests For Draft Canonical Paths

**Files:**
- Create: `tests/test_dataset_review_gate.py`
- Test: `tests/test_dataset_review_gate.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dataset_review_gate.py` with this content:

```python
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.services.dataset_edit_service import resolve_editable_path
from backend.services.dataset_review_service import write_draft
from backend.services.report_store import ReportStore
from backend.services.run_service import RunService


def valid_row(sample_id: str = "sample-1") -> dict[str, object]:
    return {
        "id": sample_id,
        "vendor": "Cisco",
        "model": "Catalyst 1200",
        "scenario_type": "配置查询",
        "topic": "VLAN",
        "difficulty": "基础",
        "question": "如何查看 VLAN 配置？",
        "alternative_queries": ["查看 VLAN 的命令是什么？"],
        "expected_documents": ["Catalyst 1200 用户手册.pdf"],
        "expected_sections": ["VLAN 配置"],
        "expected_keywords": ["VLAN", "show"],
        "evaluation_focus": "应召回 VLAN 配置相关章节",
    }


class DatasetReviewPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name) / "AINE2-KB-Eval"
        self.generated_dir = self.project_root / "datasets" / "generated"
        self.generated_dir.mkdir(parents=True)
        (self.project_root / "reports").mkdir(parents=True)
        self.output_path = self.generated_dir / "cisco_c1200_generated.jsonl"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_resolve_editable_path_accepts_canonical_path_when_only_draft_exists(self) -> None:
        write_draft(self.output_path, [valid_row()])

        resolved = resolve_editable_path(
            "datasets/generated/cisco_c1200_generated.jsonl",
            [self.project_root / "datasets"],
        )

        self.assertEqual(resolved, self.output_path.resolve())

    def test_list_datasets_reports_draft_under_canonical_main_path(self) -> None:
        write_draft(self.output_path, [valid_row()])
        service = RunService(self.project_root, ReportStore(self.project_root / "reports"))

        items = service.list_datasets()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "cisco_c1200_generated")
        self.assertEqual(items[0]["path"], "datasets/generated/cisco_c1200_generated.jsonl")
        self.assertEqual(items[0]["draft_path"], "datasets/generated/cisco_c1200_generated.draft.jsonl")
        self.assertEqual(items[0]["review_status"], "draft")
        self.assertEqual(items[0]["sample_count"], 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m unittest tests.test_dataset_review_gate -v
```

Expected: the editable path test fails with `DATASET_NOT_FOUND`, and the list test reports the draft path as the dataset path or reports an unexpected item count.

- [ ] **Step 3: Commit the failing tests**

Run:

```powershell
git add tests/test_dataset_review_gate.py
git commit -m "test:补充评测集审核关卡路径测试"
```

---

### Task 2: Backend Canonical Draft Path Support

**Files:**
- Modify: `backend/services/dataset_edit_service.py`
- Modify: `backend/services/run_service.py`
- Test: `tests/test_dataset_review_gate.py`

- [ ] **Step 1: Update editable path resolution**

In `backend/services/dataset_edit_service.py`, import `draft_path_for`:

```python
from backend.services.dataset_review_service import draft_path_for
```

Replace the candidate loop in `resolve_editable_path()` with this version:

```python
    for candidate in candidates:
        try:
            path = candidate.resolve(strict=True)
        except (FileNotFoundError, OSError):
            path = candidate.resolve(strict=False)
            draft = draft_path_for(path)
            if path.suffix.lower() != ".jsonl":
                continue
            if draft.exists() and any(root == path or root in path.parents for root in allowed_roots):
                return path
            continue
        if path.suffix.lower() != ".jsonl":
            raise DatasetEditError(
                "DATASET_PATH_FORBIDDEN",
                "评测集必须是 JSONL 文件",
                {"eval_file": eval_file},
            )
        if not any(root == path or root in path.parents for root in allowed_roots):
            raise DatasetEditError(
                "DATASET_PATH_FORBIDDEN",
                "评测集路径不在允许目录内",
                {"eval_file": eval_file},
            )
        return path
```

- [ ] **Step 2: Add canonical path helpers to run service**

In `backend/services/run_service.py`, change the import to include `draft_path_for`:

```python
from backend.services.dataset_review_service import draft_path_for, read_review_state
```

Add these helper functions near `dataset_name()`:

```python
def is_draft_dataset_path(path: Path) -> bool:
    return path.name.endswith(".draft.jsonl")


def canonical_dataset_path(path: Path) -> Path:
    if not is_draft_dataset_path(path):
        return path
    return path.with_name(path.name.removesuffix(".draft.jsonl") + ".jsonl")


def metadata_source_path(path: Path) -> Path:
    if path.exists():
        return path
    draft = draft_path_for(path)
    return draft if draft.exists() else path
```

- [ ] **Step 3: Update dataset listing to use canonical paths**

In `RunService.list_datasets()`, replace the candidate collection and per-item metadata source with this structure:

```python
        candidates: list[Path] = []
        candidates.extend(sorted(self.datasets_dir.glob("*.jsonl")))
        candidates.extend(sorted((self.datasets_dir / "generated").glob("*.jsonl")))
        candidates.extend(sorted(self.docs_dir.glob("*评测数据集.jsonl")))

        canonical_candidates = [canonical_dataset_path(path) for path in candidates]

        seen: set[Path] = set()
        items: list[dict[str, Any]] = []
        for path in canonical_candidates:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            source_path = metadata_source_path(path)
            try:
                meta = dataset_metadata(source_path)
            except EvalError:
                continue
            rel_path = self.display_path(path)
            updated_at = None
            if meta.get("updated_at_epoch"):
                updated_at = now_from_epoch(float(meta["updated_at_epoch"]))
            review = read_review_state(path)
            draft = draft_path_for(path)
            items.append(
                {
                    "id": path.stem,
                    "name": dataset_name(path, meta),
                    "path": rel_path,
                    "sample_count": meta["sample_count"],
                    "vendor": meta.get("vendor", ""),
                    "model": meta.get("model", ""),
                    "version": "v0.1",
                    "updated_at": updated_at,
                    "scenario_types": meta.get("scenario_types", []),
                    "scenario_distribution": meta.get("scenario_distribution", {}),
                    "review_status": review.get("status", "unreviewed"),
                    "draft_path": self.display_path(draft) if review.get("status") == "draft" else None,
                    "reviewed_at": review.get("reviewed_at"),
                    "reviewed_by": review.get("reviewed_by"),
                    "generated_at": review.get("generated_at"),
                },
            )
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
python -m unittest tests.test_dataset_review_gate -v
```

Expected: both tests in `DatasetReviewPathTests` pass.

- [ ] **Step 5: Commit canonical path support**

Run:

```powershell
git add backend/services/dataset_edit_service.py backend/services/run_service.py
git commit -m "fix:规范待审核评测集草稿路径"
```

---

### Task 3: Backend Tests For Run Creation Gate

**Files:**
- Modify: `tests/test_dataset_review_gate.py`
- Test: `tests/test_dataset_review_gate.py`

- [ ] **Step 1: Add run gate tests**

Append these imports and test class to `tests/test_dataset_review_gate.py`:

```python
from backend.schemas import CreateRunRequest
from backend.services.run_service import RunServiceError
```

```python
class DatasetReviewRunGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name) / "AINE2-KB-Eval"
        self.generated_dir = self.project_root / "datasets" / "generated"
        self.generated_dir.mkdir(parents=True)
        (self.project_root / "reports").mkdir(parents=True)
        self.output_path = self.generated_dir / "cisco_c1200_generated.jsonl"
        self.service = RunService(self.project_root, ReportStore(self.project_root / "reports"))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def request(self) -> CreateRunRequest:
        return CreateRunRequest(
            name="review gate test",
            gateway_base_url="http://127.0.0.1:8100",
            gateway_token="",
            dataset_id="",
            eval_file="datasets/generated/cisco_c1200_generated.jsonl",
            top_k=5,
            include_alternatives=False,
            limit=0,
            sample_ids=[],
            timeout_seconds=60,
            langsmith_enabled=False,
            langsmith_project="aine2-kb-eval",
        )

    def write_main_dataset(self) -> None:
        self.output_path.write_text(json.dumps(valid_row(), ensure_ascii=False) + "\n", encoding="utf-8")

    def test_create_run_rejects_draft_dataset(self) -> None:
        write_draft(self.output_path, [valid_row()])

        with self.assertRaises(RunServiceError) as ctx:
            self.service.create_run(self.request())

        self.assertEqual(ctx.exception.code, "DATASET_REVIEW_REQUIRED")
        self.assertEqual(ctx.exception.detail["review_status"], "draft")
        self.assertEqual(
            ctx.exception.detail["draft_path"],
            "datasets/generated/cisco_c1200_generated.draft.jsonl",
        )

    def test_create_run_rejects_unreviewed_dataset(self) -> None:
        self.write_main_dataset()

        with self.assertRaises(RunServiceError) as ctx:
            self.service.create_run(self.request())

        self.assertEqual(ctx.exception.code, "DATASET_REVIEW_REQUIRED")
        self.assertEqual(ctx.exception.detail["review_status"], "unreviewed")

    def test_create_run_allows_reviewed_dataset(self) -> None:
        self.write_main_dataset()
        review_meta = self.output_path.with_name("cisco_c1200_generated.review.json")
        review_meta.write_text(
            json.dumps(
                {
                    "status": "reviewed",
                    "reviewed_at": "2026-06-12T15:00:00+08:00",
                    "reviewed_by": "tester",
                    "generated_at": "2026-06-12T14:30:00+08:00",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        manifest, config = self.service.create_run(self.request())

        self.assertEqual(manifest["status"], "queued")
        self.assertEqual(config.eval_file, self.output_path.resolve())
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m unittest tests.test_dataset_review_gate -v
```

Expected: `draft` and `unreviewed` cases fail because `create_run()` does not reject them yet.

- [ ] **Step 3: Commit failing run gate tests**

Run:

```powershell
git add tests/test_dataset_review_gate.py
git commit -m "test:补充评测集运行审核拦截测试"
```

---

### Task 4: Backend Run Creation Enforcement

**Files:**
- Modify: `backend/services/run_service.py`
- Test: `tests/test_dataset_review_gate.py`

- [ ] **Step 1: Add review gate helper**

Add this method inside `RunService` before `create_run()`:

```python
    def ensure_dataset_reviewed(self, eval_file: Path) -> None:
        review = read_review_state(eval_file)
        status = str(review.get("status") or "unreviewed")
        if status == "reviewed":
            return
        draft = draft_path_for(eval_file)
        raise RunServiceError(
            "DATASET_REVIEW_REQUIRED",
            "评测集尚未通过人工审核，请先在评测集编辑器中标记为已审核",
            {
                "eval_file": self.display_path(eval_file),
                "review_status": status,
                "draft_path": self.display_path(draft) if draft.exists() else None,
            },
        )
```

- [ ] **Step 2: Call the gate in create_run**

In `RunService.create_run()`, immediately after resolving `eval_file`, add:

```python
        self.ensure_dataset_reviewed(eval_file)
```

The beginning of the method should read:

```python
    def create_run(self, request: CreateRunRequest) -> tuple[dict[str, Any], EvalRunConfig]:
        eval_file = self.resolve_eval_file(request.eval_file)
        self.ensure_dataset_reviewed(eval_file)
        name = request.name.strip() or f"{eval_file.stem} Top{request.top_k}"
```

- [ ] **Step 3: Allow resolve_eval_file to recognize draft-only canonical paths**

In `RunService.resolve_eval_file()`, update the candidate loop so a canonical path with an existing draft can be resolved and then rejected by the review gate:

```python
        for candidate in candidates:
            path = candidate.resolve()
            if not path.exists():
                draft = draft_path_for(path)
                if draft.exists() and path.suffix.lower() == ".jsonl":
                    if not any(root == path or root in path.parents for root in allowed_roots):
                        raise RunServiceError("INVALID_EVAL_FILE", "评测集路径不在允许目录内", {"eval_file": value})
                    return path
                continue
            if path.suffix.lower() != ".jsonl":
                raise RunServiceError("INVALID_EVAL_FILE", "评测集必须是 JSONL 文件", {"eval_file": value})
            if not any(root == path or root in path.parents for root in allowed_roots):
                raise RunServiceError("INVALID_EVAL_FILE", "评测集路径不在允许目录内", {"eval_file": value})
            return path
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
python -m unittest tests.test_dataset_review_gate -v
```

Expected: all tests in `tests.test_dataset_review_gate` pass.

- [ ] **Step 5: Run existing backend tests**

Run:

```powershell
python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit backend enforcement**

Run:

```powershell
git add backend/services/run_service.py
git commit -m "feat:强制评测集审核后才能运行"
```

---

### Task 5: Preserve Backend Error Codes In Frontend API

**Files:**
- Modify: `frontend/src/api.ts`
- Verify: `frontend`

- [ ] **Step 1: Extend RequestError**

In `frontend/src/api.ts`, replace the `RequestError` interface with:

```ts
export interface RequestError extends Error {
  status?: number;
  code?: string;
  detail?: Record<string, unknown>;
  validation_errors?: unknown[];
}
```

- [ ] **Step 2: Capture code and detail in request()**

Inside `request<T>()`, replace the non-OK response parsing block with:

```ts
    let code = "";
    let detail: Record<string, unknown> | undefined;
    try {
      const error = await response.json();
      if (error?.message) message = error.message;
      if (error?.code) code = String(error.code);
      if (error?.detail && typeof error.detail === "object" && !Array.isArray(error.detail)) {
        detail = error.detail as Record<string, unknown>;
      }
      if (Array.isArray(error?.detail?.validation_errors)) {
        validationErrors = error.detail.validation_errors;
      } else if (Array.isArray(error?.validation_errors)) {
        validationErrors = error.validation_errors;
      }
    } catch {
      // Ignore non-json errors from dev proxies.
    }
    const err = new Error(message) as RequestError;
    err.status = response.status;
    if (code) err.code = code;
    if (detail) err.detail = detail;
    if (validationErrors) err.validation_errors = validationErrors;
    throw err;
```

- [ ] **Step 3: Run frontend build**

Run:

```powershell
cd frontend
npm run build
```

Expected: TypeScript and Vite build complete successfully.

- [ ] **Step 4: Commit API error metadata**

Run:

```powershell
cd ..
git add frontend/src/api.ts
git commit -m "fix:保留前端接口错误码详情"
```

---

### Task 6: Frontend Home Review Gate

**Files:**
- Modify: `frontend/src/pages/Home.tsx`
- Modify: `frontend/src/styles.css`
- Verify: `frontend`

- [ ] **Step 1: Import Link and remove unused icons if needed**

In `frontend/src/pages/Home.tsx`, change the router import:

```ts
import { Link, useLocation, useNavigate } from "react-router-dom";
```

If `CheckCircle2`, `Gauge`, or `formatPercent` are only used inside the hidden commented block and TypeScript reports unused imports, remove them from active imports.

- [ ] **Step 2: Add review gate derived state**

After `selectedDataset`, add:

```ts
  const selectedReviewStatus = selectedDataset?.review_status || "unreviewed";
  const selectedDatasetNeedsReview = Boolean(selectedDataset && selectedReviewStatus !== "reviewed");
  const selectedDatasetReviewText =
    selectedReviewStatus === "draft"
      ? `该评测集存在待审核草稿${selectedDataset?.draft_path ? `（${selectedDataset.draft_path}）` : ""}，请先完成人工审核。`
      : "该评测集尚未写入人工审核记录，请先打开编辑器确认并标记为已审核。";
```

- [ ] **Step 3: Replace submit-time confirmation with a hard stop**

In `handleSubmit()`, remove the `window.confirm()` block and replace it with:

```ts
    if (selectedDatasetNeedsReview) {
      setError("评测集尚未通过人工审核，请先在评测集编辑器中标记为已审核。");
      return;
    }
```

- [ ] **Step 4: Replace draft warning with review gate banner**

Replace the current draft-only warning and reviewed success blocks with:

```tsx
            {selectedDatasetNeedsReview && selectedDataset && (
              <div className="review-gate-callout" data-status={selectedReviewStatus}>
                <div>
                  <strong>需要人工审核</strong>
                  <span>{selectedDatasetReviewText}</span>
                </div>
                <Link
                  className="ghost-button inline"
                  to={`/datasets/${encodeURIComponent(selectedDataset.path)}/editor`}
                >
                  去审核
                </Link>
              </div>
            )}
            {selectedDataset && selectedReviewStatus === "reviewed" && (
              <div className="success-line" style={{ marginTop: 8 }}>
                已通过人工审核
                {selectedDataset.reviewed_at ? `（${selectedDataset.reviewed_at}）` : ""}
                。
              </div>
            )}
```

- [ ] **Step 5: Disable the submit button when review is required**

Replace the submit button with:

```tsx
            <button
              className="primary-button"
              type="submit"
              disabled={submitting || loading || selectedDatasetNeedsReview}
              title={selectedDatasetNeedsReview ? "请先完成人工审核" : "开始评测"}
            >
              {submitting ? <Loader2 size={18} className="spin" /> : <Play size={18} />}
              {selectedDatasetNeedsReview ? "待审核，不能评测" : submitting ? "正在创建评测" : "开始评测"}
            </button>
```

- [ ] **Step 6: Add minimal callout styles**

In `frontend/src/styles.css`, add:

```css
.review-gate-callout {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 10px;
  padding: 12px 14px;
  border: 1px solid #f59e0b;
  border-radius: 8px;
  background: #fffbeb;
  color: #78350f;
}

.review-gate-callout > div {
  display: grid;
  gap: 4px;
}

.review-gate-callout strong {
  font-size: 13px;
}

.review-gate-callout span {
  font-size: 13px;
  line-height: 1.5;
}
```

- [ ] **Step 7: Run frontend build**

Run:

```powershell
cd frontend
npm run build
```

Expected: build succeeds.

- [ ] **Step 8: Commit home gate UI**

Run:

```powershell
cd ..
git add frontend/src/pages/Home.tsx frontend/src/styles.css
git commit -m "feat:首页禁止未审核评测集运行"
```

---

### Task 7: Frontend Generation Success Review CTA

**Files:**
- Modify: `frontend/src/pages/Datasets.tsx`
- Verify: `frontend`

- [ ] **Step 1: Remove the generated dataset run shortcut**

In `frontend/src/pages/Datasets.tsx`, remove the `goToHomeWithDataset()` function and the button that calls it. Keep the `pendingDataset` state so the generated result can show a review action.

- [ ] **Step 2: Make the generated result action point only to review**

Replace the pending dataset action block with:

```tsx
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                <Link
                  className="primary-button"
                  to={`/datasets/${encodeURIComponent(pendingDataset.path)}/editor`}
                >
                  <Edit3 size={14} />
                  去审核草稿
                </Link>
              </div>
```

- [ ] **Step 3: Update the pending dataset status copy**

In the same block, use this message:

```tsx
                <div style={{ color: "#b45309", fontSize: 12, marginTop: 6, fontWeight: 700 }}>
                  状态：草稿待审核。审核通过前不能发起评测。
                </div>
```

- [ ] **Step 4: Remove unused imports**

If TypeScript reports `ArrowRight` is unused in `frontend/src/pages/Datasets.tsx`, remove it from the `lucide-react` import list.

- [ ] **Step 5: Run frontend build**

Run:

```powershell
cd frontend
npm run build
```

Expected: build succeeds.

- [ ] **Step 6: Commit generation CTA**

Run:

```powershell
cd ..
git add frontend/src/pages/Datasets.tsx
git commit -m "feat:生成评测集后引导人工审核"
```

---

### Task 8: Documentation And Full Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/API契约.md`
- Verify: backend and frontend

- [ ] **Step 1: Update README workflow**

In `README.md`, update the usage flow so the dataset step reads:

```markdown
2. 选择现有已审核 JSONL 评测集，或从 PDF / Markdown 生成评测集。
3. 新生成的评测集会先进入草稿状态，必须在评测集编辑器中人工复核并标记为已审核。
4. 只有已审核评测集可以发起评测运行。
```

- [ ] **Step 2: Update API contract for run creation**

In `docs/API契约.md`, under `POST /api/runs`, add this rule:

```markdown
评测运行创建前会强制检查评测集审核状态。只有 `reviewed` 状态允许创建运行；`draft` 和 `unreviewed` 会返回 `DATASET_REVIEW_REQUIRED`，前端应引导用户进入评测集编辑器完成审核。
```

- [ ] **Step 3: Run backend tests**

Run:

```powershell
python -m unittest discover -s tests -v
```

Expected: all backend tests pass.

- [ ] **Step 4: Run frontend build**

Run:

```powershell
cd frontend
npm run build
```

Expected: TypeScript and Vite build complete successfully.

- [ ] **Step 5: Check git status**

Run:

```powershell
cd ..
git status --short
```

Expected: only intended documentation files are unstaged after this task, plus any unrelated pre-existing user changes remain visible.

- [ ] **Step 6: Commit documentation updates**

Run:

```powershell
git add README.md docs/API契约.md
git commit -m "docs:说明评测集人工审核强制关卡"
```

## Plan Self-Review

- Spec coverage: backend hard gate is covered by Tasks 3 and 4; frontend disabled state and review link are covered by Task 6; generation success review path is covered by Task 7; canonical draft handling is covered by Tasks 1 and 2; API and README documentation are covered by Task 8.
- Placeholder scan: the plan contains no deferred-work markers, vague implementation steps, or unnamed files.
- Type consistency: backend uses `review_status`, `draft_path`, and `DATASET_REVIEW_REQUIRED` consistently; frontend uses existing `DatasetInfo.review_status`, `DatasetInfo.draft_path`, and `RequestError` extensions consistently.
