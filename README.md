# Dify-KB-Eval

语言：简体中文 | [English](README.en.md)

Dify-KB-Eval 是一个面向 Dify 知识库的检索质量评测平台。它把评测集管理、Dify 知识库 API 调用、召回指标计算、失败样本分析和多次运行对比放在一个独立工具里，适合研发、测试和交付前验证知识库召回效果。

运行元数据存放在本项目自带的 PostgreSQL 中，评测集、检索明细和下载产物保留在本地目录。使用时只需要配置 Dify API 地址、Dify API Key 和目标知识库。

## 核心能力

| 能力 | 说明 |
| --- | --- |
| Dify 知识库检索 | 直连 Dify Knowledge Base API，支持知识库列表、知识库匹配和 Top K 检索 |
| Dify 连接配置 | Dify API 地址和 Key 成对保存在后端数据库，用于刷新后回填、下拉复用和删除历史连接 |
| 评测集生成 | 从 PDF / Markdown 生成 JSONL 样本，PDF 固定使用本地 MarkItDown 解析 |
| 人工审核门禁 | 自动生成的样本先进入 `draft`，必须在编辑器里标记为 `reviewed` 后才能评测 |
| 指标计算 | 计算文档、章节、关键词和综合内容命中，以及 Recall、MRR、Precision、NDCG、空结果率和耗时 |
| 运行追踪 | FastAPI 后端异步执行评测，前端通过 SSE 查看进度，失败样本和日志可追溯 |
| 横向对比 | 在同一评测集下比较不同 embedding / rerank 标签、Top K 和样本规模的表现 |
| 本地报告 | 生成 `results.jsonl`、`results.csv`、`console.log` 等可下载产物，摘要和报告正文落库 |
| Docker 部署 | 支持一键容器部署和离线部署包，后端、前端、PostgreSQL 使用独立 Docker 网络互联 |
| 统一日志 | 后端、runner 子进程和请求链路写入 `logs/`，日志自动带 `request_id` / `run_id`，便于排查问题 |

## 适用场景

用它回答这些问题：

- 新知识库上线前，Top K 是否稳定召回正确文档和章节？
- 更换 embedding、rerank 或切分策略后，召回、MRR、延迟有没有变好？
- 哪些问题、文档或场景类型最容易召回失败？
- 同一份评测集在多套 Dify 知识库配置下，哪一组更适合交付？

## 系统架构

![](img/下载.png)



| 内容 | 位置 |
| --- | --- |
| 运行状态、进度、指标摘要、Markdown 报告正文 | PostgreSQL |
| 每条 query 的检索明细、CSV、控制台日志 | `reports/<run_id>/` |
| 主评测集、自动生成评测集、草稿和审核元信息 | `datasets/`、`datasets/generated/` |
| 上传的源文档和中间 Markdown | `generated_sources/` |

## 功能入口

| 入口 | 主要用途 |
| --- | --- |
| 评测台 | 填写或选择历史 Dify 连接，拉取知识库，配置 Top K、样本上限和对比标签并发起评测 |
| 评测集 | 查看 `datasets/` 与 `datasets/generated/` 中的 JSONL，选择 PDF / Markdown 源文件生成草稿，删除评测集前自动备份 |
| 评测集编辑器 | 逐行编辑样本、查看校验错误、保存草稿或主文件，并提交人工审核 |
| 运行详情 | 通过 SSE 查看运行进度，查看失败样本、指标、Markdown 报告和下载产物 |
| 历史运行 | 按状态和 Dify 地址查看运行记录，支持重命名、删除和取消运行 |
| 分析对比 | 按同一评测集聚合多次完成的运行，对比 embedding / rerank、Top K、样本数和综合评分 |

## 快速开始

### 环境要求

- Python `>= 3.12,<3.14`
- [uv](https://docs.astral.sh/uv/)
- Node.js 和 npm
- Docker，可选但推荐，用于本地 PostgreSQL 或容器部署
- 已完成索引的 Dify 知识库
- Dify Knowledge Base API Key

### 本地一键启动

如果还没有 `.env`，先复制示例配置：

```powershell
Copy-Item .env.example .env
```

启动后端、前端和本地数据库：

```powershell
.\start.ps1
```

或在 `cmd` 中执行：

```cmd
start.bat
```

脚本会检查 `uv` / `npm`，在 Docker 可用时启动 PostgreSQL，安装缺失依赖，并分别启动：

- 后端：`http://127.0.0.1:8200`
- 前端：`http://127.0.0.1:5598`，如果 `frontend/.env` 或 `frontend/.env.local` 配置了 `DEV_PORT`，则以该端口为准

只想预览前端页面时可以启用 mock，不依赖后端、数据库或 Dify：

```powershell
.\start.ps1 -Mock
```

或：

```cmd
start.bat -Mock
```

Linux / macOS：

```bash
cp .env.example .env
bash ./start.sh
```

`start.sh` 默认在前台同时跑后端和前端，按 Ctrl+C 一次性停止两者。只想预览前端、不依赖后端和 Dify 时：

```bash
bash ./start.sh --mock
```

### 手动启动

启动 PostgreSQL 和后端：

```powershell
Copy-Item .env.example .env
docker compose up -d db
uv sync
uv run uvicorn backend.app:app --reload --host 127.0.0.1 --port 8200
```

Linux / macOS：

```bash
cp .env.example .env
docker compose up -d db
uv sync
uv run uvicorn backend.app:app --reload --host 127.0.0.1 --port 8200
```

> Windows 上 `uv run uvicorn` 偶尔会报 `uv trampoline failed to canonicalize script path`。遇到时直接用 venv 里的 Python 调用 uvicorn 模块：
>
> ```powershell
> .venv\Scripts\python.exe -m uvicorn backend.app:app --reload --host 127.0.0.1 --port 8200
> ```

另开一个终端启动前端：

```powershell
cd frontend
npm install
$env:VITE_API_BASE_URL = "http://127.0.0.1:8200"
npm run dev
```

Linux / macOS：

```bash
cd frontend
npm install
VITE_API_BASE_URL="http://127.0.0.1:8200" npm run dev
```

浏览器访问 Vite 输出的本地地址，默认是 `http://127.0.0.1:5598`。

## Docker 部署

### 在线一键部署

如果希望完整用容器部署后端、前端和 PostgreSQL，在项目根目录执行：

```powershell
.\deploy-docker.ps1
```

Linux / macOS：

```bash
bash ./deploy-docker.sh
```

部署脚本会自动构建后端/前端镜像，启动 `db` / `backend` / `frontend` 三个服务，并等待 `http://127.0.0.1:5598/api/health` 就绪后打开浏览器。

默认创建 `dify-kb-eval-net` 网络，容器内可通过 `db:5432`、`backend:8200` 或容器名 `dify-kb-eval-db`、`dify-kb-eval-backend` 互相访问。

首次部署到空 PostgreSQL 时不需要手工建表：PostgreSQL 容器会按 `POSTGRES_DB` 创建数据库，后端容器入口会等待数据库就绪，发现空库后自动创建当前 ORM 表结构，并把 Alembic 版本 `stamp head`。相关开关是 `.env` 里的 `RUN_DB_INIT_ON_EMPTY=true` 和 `RUN_DB_STAMP_HEAD_ON_INIT=true`。

默认使用国内镜像源：

| 类型 | 默认值 |
| --- | --- |
| Docker 基础镜像 | `m.daocloud.io/docker.io/library/...` |
| apt | `mirrors.aliyun.com` |
| PyPI | `https://mirrors.aliyun.com/pypi/simple/` |
| npm | `https://registry.npmmirror.com` |

如需切换镜像源，复制 `.env.example` 为 `.env` 后修改 `POSTGRES_IMAGE`、`PYTHON_IMAGE`、`NODE_IMAGE`、`NGINX_IMAGE`、`APT_MIRROR`、`PYPI_INDEX_URL`、`NPM_REGISTRY` 等变量。

停止容器：

```powershell
.\deploy-docker.ps1 -Down
```

### 离线部署包

离线部署分两步：先在有网络的机器上构建离线包，再把包复制到无网络机器上加载运行。构建机和离线机器都只需要安装并启动 Docker Desktop / Docker Engine；离线机器不需要 Python、uv、Node.js 或 npm。

Windows 构建机：

```powershell
cd C:\Users\17651\Desktop\AI2\Dify-KB-Eval
.\build-offline-package.ps1
```

构建完成后，离线包会输出到：

```text
C:\Users\17651\Desktop\AI2\Dify-KB-Eval\offline-packages\
```

文件名类似：

```text
dify-kb-eval-offline-20260625-153000.zip
```

如果要指定交付版本号：

```powershell
.\build-offline-package.ps1 -Tag v1.0.0
```

Linux / macOS 构建机：

```bash
bash ./build-offline-package.sh
```

脚本会构建后端/前端镜像，拉取 PostgreSQL 镜像，把三份镜像导出到 `offline-packages/dify-kb-eval-offline-<时间>/images/*.tar`，并生成离线包。包内自带：

```text
images/
  backend.tar
  frontend.tar
  postgres.tar
docker-compose.offline.yml
.env.offline
deploy-offline.ps1
deploy-offline.sh
datasets/
docs/
config/
```

把离线包复制到无网络 Windows 机器，解压后在解压目录执行：

```powershell
.\deploy-offline.ps1
```

Linux / macOS 机器：

```bash
bash ./deploy-offline.sh
```

离线部署脚本会执行 `docker load` 加载镜像，然后用 `pull_policy: never` 的 compose 文件启动服务，不会访问外网。默认访问地址仍是 `http://127.0.0.1:5598`。

离线环境首次启动同样会自动初始化空 PostgreSQL，不需要额外执行 SQL 或 Alembic 命令。

如果需要把历史运行产物、上传源文件等也打进包里：

```powershell
.\build-offline-package.ps1 -Tag v1.0.0 -IncludeRuntimeData
```

## 基本流程

1. 在 Dify 中准备知识库，并确认文档索引完成。
2. 启动本项目的 PostgreSQL、后端和前端。
3. 将已有 JSONL 放入 `datasets/` 或 `datasets/generated/`，或在“评测集”页面从 PDF / Markdown 生成评测集。
4. 打开评测集编辑器，逐行复核并提交审核。
5. 回到“评测台”，填写 Dify API 地址和 Dify API Key。
6. 通过“选 KB”下拉选择目标知识库，自动回填 embedding / rerank 对比标签。
7. 先用小样本运行，例如 `limit=3`，确认链路和报告正常。
8. 将样本上限设为 `0` 执行全量评测。
9. 在详情页查看指标、失败样本、报告和下载产物。
10. 在“分析对比”页比较同一评测集下的多次运行，优先参考综合分、等级、风险、短板和判断依据。

`limit=0` 表示不限制样本数量。

## 评测集格式

评测集使用 UTF-8 JSONL，每行一个样本。最小示例：

```json
{
  "id": "KB-EVAL-001",
  "vendor": "示例厂商",
  "model": "示例型号",
  "scenario_type": "故障恢复",
  "topic": "控制台密码恢复",
  "difficulty": "中等",
  "question": "控制台密码丢失后如何恢复？",
  "alternative_queries": [
    "控制台密码忘了怎么办？"
  ],
  "expected_documents": [
    "01-01 常见系统操作.pdf"
  ],
  "expected_sections": [
    "控制台登录密码丢失后如何恢复"
  ],
  "expected_keywords": [
    "控制台",
    "密码恢复"
  ],
  "evaluation_focus": "应命中密码恢复章节，并包含关键恢复步骤。"
}
```

审核状态：

| 状态 | 含义 |
| --- | --- |
| `unreviewed` | 历史样本或尚未写入审核记录 |
| `draft` | 存在待审核草稿，不能直接创建评测运行 |
| `reviewed` | 已通过人工审核，可以用于评测 |

字段说明和质量要求见 [docs/数据集规范.md](docs/数据集规范.md)。

## 评测集生成

支持从目录或浏览器上传的 PDF / Markdown 生成评测集。推荐源文档结构：

```text
示例厂商/示例型号/
  01-01 常见系统操作.pdf
  01-02 常见维护操作.pdf
  md/
    01-01 常见系统操作.md
    01-02 常见维护操作.md
```

页面支持选择包含 PDF / Markdown 的文件夹，也支持只上传单个 PDF。浏览器上传的源文件会保存到 `generated_sources/<厂商>/<型号>/`，处理后的 Markdown 保存到其 `md/` 子目录。

PDF 解析固定使用本地 MarkItDown 包或 CLI，不依赖外部解析服务。启用 `reuse_existing_markdown` 时会优先复用同名 Markdown；没有同名 Markdown 时才调用 MarkItDown。可通过请求字段 `markitdown_command` 或环境变量 `MARKITDOWN_COMMAND` 指定自定义 CLI 模板。

生成结果默认写入 `datasets/generated/`。新样本会先保存为同名 `.draft.jsonl`，审核通过后才覆盖主 JSONL，并写入 `<name>.review.json`。

## 指标

评测会按照当前 Top K 生成 `@1`、`@3`、`@5` 和当前 K 指标，并按 `scenario_type` 输出分组统计。

| 指标 | 含义 |
| --- | --- |
| `document_recall@k` | Top K 中是否命中期望文档 |
| `section_recall@k` | Top K 中是否命中期望章节，支持章节别名和斜杠分隔 |
| `keyword_recall@k` | Top K 中是否满足关键词命中条件 |
| `content_recall@k` | 文档、章节或关键词任一命中 |
| `content_precision@k` | Top K 结果中内容命中的比例 |
| `content_ndcg@k` | 基于命中位置计算的归一化折损累计增益 |
| `*_mrr` | 对应命中类型的平均倒数排名 |
| `empty_result_rate` | 未返回任何检索结果的 query 占比 |
| `avg_latency_ms` / `p95_latency_ms` | 成功 query 的平均耗时和 P95 耗时 |
| `error_queries` | 调用 Dify 失败的 query 数 |

严格的段落级 Recall 依赖评测集完整标注所有可接受答案。当前实现使用文档、章节和关键词规则生成 `content_hit`。

### 综合评分模型

分析对比页会在原始指标之上生成一个面向交付决策的综合评分。它不会替代明细指标，而是把常用判断口径统一到一行里，方便在多组 embedding / rerank / Top K 结果之间快速排序。

| 组成 | 说明 |
| --- | --- |
| Content 质量 | 以 `content_recall@5`、`content_mrr`、`content_ndcg@5` 为主要信号；缺少 Content 指标时回退到 Document 指标 |
| 辅助命中 | 纳入 Section / Document / Keyword 召回或排序信号，避免只看内容命中而忽略章节定位和关键词覆盖 |
| 风险惩罚 | 对 `empty_result_rate`、`error_queries`、未完成 query、P95 延迟过高和样本数过少扣分 |
| 等级 | 输出 `优秀`、`可用`、`风险可用`、`不可用` 或 `缺指标`，同时给出 `通过`、`需复核`、`阻断` 等风险标签 |
| 判断依据 | 在表格和导出结果中展示主要短板、风险项和关键指标摘要，便于复盘为什么某行是最佳候选 |

组内最佳行和 CSV / Excel 导出均使用同一套综合评分口径。若要调整权重，请修改 `frontend/src/pages/runCompareScoring.ts` 并补充 `runCompareScoring.test.mjs`。

## 配置

复制 `.env.example` 后按需修改：

```powershell
Copy-Item .env.example .env
```

常用配置：

| 变量 | 说明 |
| --- | --- |
| `DATABASE_URL` | 后端数据库连接。默认指向 `docker-compose.yml` 中的本地 PostgreSQL：`127.0.0.1:5555` |
| `RUN_DB_BOOTSTRAP` | 开发环境可为 `true`；容器部署默认会自动初始化空库 |
| `RUN_DB_MIGRATIONS` | 设为 `true` 时，后端容器入口会在启动前执行 `alembic upgrade head`。默认 `true`（`docker-compose*.yml` 和容器入口脚本中的兜底仍是 `false`，因此离线部署必须显式通过 `.env` / `.env.offline` 传入 `true` 才会执行迁移） |
| `RUN_DB_INIT_ON_EMPTY` | 容器启动时发现空库后自动创建表 |
| `RUN_DB_STAMP_HEAD_ON_INIT` | 空库自动建表后把 Alembic 版本标记到 head |
| `LOG_LEVEL` | 日志级别：`DEBUG`、`INFO`、`WARNING`、`ERROR` |
| `LOG_DIR` / `LOG_TO_FILE` | 日志目录与是否写文件 |
| `LOG_MAX_BYTES` / `LOG_BACKUP_COUNT` | 日志轮转大小和备份数量 |
| `MARKITDOWN_COMMAND` | 可选的 MarkItDown CLI 模板，支持 `{input}` / `{output}` 占位 |
| `EVAL_RUNNER_CONCURRENCY` | runner 子进程并发数，默认 `8` |
| `EVAL_RUNNER_TICK_MS` | runner 领取队列的间隔，默认 `500` |
| `EVAL_RUNNER_SUBPROCESS` | 设为 `disabled` 可回退到后端内联执行 |
| `POSTGRES_IMAGE` | Docker / 离线包使用的 PostgreSQL 镜像 |
| `PYTHON_IMAGE` / `NODE_IMAGE` / `NGINX_IMAGE` | 构建后端和前端镜像时使用的基础镜像 |

前端默认代理到 `http://127.0.0.1:8200`。如需修改：

```powershell
cd frontend
$env:VITE_API_BASE_URL = "http://127.0.0.1:8200"
npm run dev
```

Dify API 地址和 Key 会以明文成对保存在后端数据库的历史连接配置中，用于刷新后自动回填和下拉复用。后端不会把 token 写入运行产物、报告正文或公开运行配置，但仍建议只在可信本机或可信内网环境使用真实凭据。

## 日志

默认开启文件日志，目录由 `LOG_DIR` 控制，未配置时写入 `logs/`：

| 文件 | 内容 |
| --- | --- |
| `logs/backend.log` | FastAPI 启停、HTTP 请求、数据库初始化、runner supervisor / watchdog 状态 |
| `logs/backend.error.log` | 后端异常和 5xx 相关错误 |
| `logs/runner.log` | runner 子进程启动、领任务、单次 run 开始 / 完成 / 失败 |
| `logs/runner.error.log` | runner 子进程异常 |
| `reports/<run_id>/console.log` | 单次评测面向用户的执行日志和检索过程摘要 |

HTTP 请求日志包含 `request_id`，响应头也会返回 `X-Request-ID`。评测执行日志包含 `run_id`，因此排查时可以先在前端复制 run id，再到 `logs/runner.log`、`logs/backend.log` 和 `reports/<run_id>/console.log` 里交叉搜索。

## API 概览

后端默认地址是 `http://127.0.0.1:8200`，业务接口统一使用 `/api` 前缀。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/api/datasets` | 评测集列表 |
| `POST` | `/api/datasets/generate` | 从服务端路径生成评测集 |
| `POST` | `/api/datasets/generate/upload` | 上传源文档并生成评测集 |
| `DELETE` | `/api/datasets/{path:path}` | 删除评测集及其草稿、审核元信息 |
| `GET` / `PUT` | `/api/dataset-rows/{path}` | 读取或保存评测集行数据 |
| `POST` | `/api/dataset-rows/{path}/review` | 提交人工审核 |
| `GET` | `/api/dataset-rows/{path}/export` | 导出 JSONL 文本 |
| `GET` | `/api/knowledge-bases` | 拉取 Dify 知识库列表 |
| `GET` | `/api/dify-connections` | 历史 Dify 连接配置列表 |
| `POST` | `/api/dify-connections` | 保存 Dify API 地址和 Key 配对 |
| `DELETE` | `/api/dify-connections/{config_id}` | 删除历史连接配置 |
| `POST` | `/api/runs` | 创建评测运行 |
| `GET` | `/api/runs` | 历史运行列表 |
| `GET` | `/api/runs/compare` | 横向对比数据 |
| `GET` | `/api/runs/{run_id}` | 运行详情 |
| `GET` | `/api/runs/{run_id}/events` | SSE 运行进度 |
| `PATCH` | `/api/runs/{run_id}` | 修改运行名称 |
| `POST` | `/api/runs/{run_id}/labels` | 修改 embedding / rerank 对比标签 |
| `GET` | `/api/runs/{run_id}/report` | 获取 Markdown 报告 |
| `GET` | `/api/runs/{run_id}/artifacts/{name}` | 下载运行产物 |
| `DELETE` | `/api/runs/{run_id}` | 删除或取消运行 |
| `POST` | `/api/langsmith/datasets/sync` | 可选：同步 LangSmith 数据集 |
| `POST` | `/api/langsmith/experiments/run` | 可选：创建 LangSmith 实验运行 |

完整请求与响应见 [docs/API契约.md](docs/API契约.md)。

## 项目目录结构

```text
Dify-KB-Eval/
├── backend/                  # FastAPI 路由、Pydantic schema、数据库与服务层
│   ├── app.py                # 应用入口、lifespan、API 路由、SSE
│   ├── db/                   # SQLAlchemy session、ORM model、Alembic
│   └── services/             # run、dataset、artifact、LangSmith、runner 服务
├── config/                   # MinerU 等配置示例
├── datasets/                 # JSONL 评测集
│   └── generated/            # 自动生成的评测集
├── docker/                   # 容器入口脚本
├── docs/                     # 设计、接口、规范、操作文档
├── frontend/                 # React + TypeScript + Vite 前端
│   ├── Dockerfile            # 前端生产镜像构建
│   ├── nginx.conf            # 静态文件服务与 /api 反向代理
│   └── src/
├── generated_sources/        # 上传源文件和中间 Markdown
├── kb_eval/                  # Dify client、评测 runner、指标、报告和解析器
├── reports/                  # 运行产物与删除备份
├── scripts/                  # 迁移和维护脚本
├── tests/                    # 后端和核心逻辑测试
├── Dockerfile                # 后端镜像构建
├── docker-compose.yml        # PostgreSQL + 后端 + 前端完整容器栈
├── docker-compose.offline.yml # 离线部署 compose，不含 build / pull
├── deploy-docker.*           # Docker 一键部署/停止脚本
├── build-offline-package.*   # 构建离线部署包
├── deploy-offline.*          # 离线机器加载镜像并启动
├── pyproject.toml            # Python 项目依赖
├── start.ps1 / start.bat / start.sh   # 本地一键启动脚本
└── verify.ps1 / verify.bat / verify.sh # 验证脚本
```

## 验证与冒烟测试

提交或交付前建议执行统一验证：

```powershell
.\verify.ps1
```

Linux / macOS：

```bash
bash ./verify.sh
```

验证脚本会依次执行：

- `uv sync`
- 后端和核心逻辑 unittest
- MarkItDown 可用性检查
- 前端 helper 测试
- 前端生产构建

依赖已确认最新时可以跳过同步：

```powershell
.\verify.ps1 -SkipSync
```

```bash
bash ./verify.sh --skip-sync
```

也可以分别运行：

```powershell
uv run python -m unittest discover
cd frontend
npm run test:helpers
npm run build
```

## 故障排查

### 页面无法拉取知识库

检查 Dify API 地址和 API Key。Dify 通常使用类似 `http://localhost/v1` 的 API 根路径，Key 需要具备知识库 API 访问权限。

### 开始评测按钮不可用

常见原因是当前评测集不是 `reviewed` 状态、尚未选择目标知识库，或 Dify API 地址和 Dify API Key 不完整。进入评测集编辑器完成审核后再回到评测台。

### 运行失败且提示模型服务不可用

这类错误通常来自 Dify 背后的模型服务、rerank 服务或插件调用。可以先降低 Top K、调大超时时间，或暂停其他并发评测后重试。

### 数据库启动失败

确认 PostgreSQL 已启动，且 `.env` 中的 `DATABASE_URL` 与 `docker-compose.yml` 暴露端口一致。容器部署到空库时会自动建表；如果关闭了 `RUN_DB_INIT_ON_EMPTY`，需要自行初始化 schema。

### 历史运行或首页返回 500

先直接访问 `http://127.0.0.1:8200/api/health` 判断后端是否存活。如果健康检查正常但 `/api/runs` 返回 500，通常是数据库 schema 与当前代码不一致。执行：

```powershell
uv run alembic current
uv run alembic heads
uv run alembic upgrade head
```

升级后重新访问 `/api/runs?limit=1&offset=0`。如果仍然异常，查看 `logs/backend.error.log` 中对应 `request_id` 的堆栈。

## 文档

- [操作手册](docs/操作手册.md)
- [API 契约](docs/API契约.md)
- [数据集规范](docs/数据集规范.md)
- [持久化设计](docs/持久化设计.md)
- [技术选型与对比](docs/技术选型与对比.md)

---

© 2025 苏州云融信息技术有限公司 版权所有
