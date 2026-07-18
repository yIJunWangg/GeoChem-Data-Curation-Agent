# GeoChem Data Curation Agent — 多 Agent 开发分工计划（修订版）

> 历史计划说明：本文中的 PySide6 桌面 UI 工作包已由 React + FastAPI Web 实现取代，不再作为当前开发路线。

## 0. 修订结论

### 0.1 核心修订点

1. **"开发 Agent"和"运行时 Agent"概念分离**。开发分工使用"工作包 / Work Package (WP)"，软件内部使用 Runtime Agent。
2. **技术栈保留扩展接口**。V1 不引入 Playwright / camelot / tabula，但保留 `BaseTableExtractor` 和 `BrowserAdapter` 接口。
3. **硬件估算更务实**。轻量任务 500MB–1GB 内存，大 PDF/Excel 可达 1–2GB。推荐 8GB 以上。
4. **数据库设计更细**。从 7 张主表扩展到 16+ 张表，支持单元格级溯源。
5. **先 CLI 跑通闭环，再做完整 UI**。UI 在数据模型稳定后推进。
6. **Memory 提前提供只读查询接口**。WP1 就实现只读 MemoryStore，WP5 再补写入和回滚。
7. **单位换算必须确定性执行**。能用规则完成的部分不交给 LLM。

### 0.2 V1 最小闭环目标

> 用户导入一篇论文 PDF 和补充材料表格后，系统能够围绕用户表头抽取候选数据，提出字段映射和单位换算建议，对不确定项进行人工审核，保存确认规则和计算过程，并导出可追溯的标准化 Excel/CSV。

### 0.3 V1 暂缓内容

1. 自动登录机构账号
2. 自动绕过验证码
3. 深度网页自动化
4. 图像数值提取
5. 多论文无人值守批处理
6. 团队协作
7. 完整复杂 UI
8. Parquet、SQL insert 等非必要导出格式
9. 复杂 PDF 表格的全自动修复

---

## 1. 技术栈

```
语言与运行时:
  Python 3.10 / 3.11

项目结构:
  pyproject.toml
  src/ 包结构
  tests/ 测试目录

桌面 UI:
  PySide6

命令行调试:
  Typer 或 argparse
  rich 可选，用于输出调试表格和日志

数据模型:
  pydantic
  dataclasses

数据库:
  SQLite
  SQLAlchemy / SQLModel 可选
  Alembic 可选，后期做数据库迁移

配置文件:
  PyYAML
  TOML / JSON

数据处理:
  pandas
  openpyxl
  python-docx

PDF 处理:
  PyMuPDF：文本、页面、元数据、图片资源、图注附近文本
  pdfplumber：文本型 PDF 表格抽取

HTML / DOI / URL:
  requests 或 httpx
  beautifulsoup4
  pandas.read_html

浏览器访问:
  webbrowser（Python 内置）：调用系统默认浏览器
  V1 不引入 Playwright，保留 BrowserAdapter 接口供后续扩展

压缩包:
  zipfile
  pathlib

AI 调用（多模型提供商）:
  openai SDK              # OpenAI、DeepSeek、Moonshot、Qwen、自定义 OpenAI 兼容接口
  anthropic SDK           # Anthropic Claude 系列
  google-genai SDK        # Google Gemini 系列（可选）
  httpx                   # Ollama / LM Studio 等本地模型 HTTP 调用
  统一 LLMClient 抽象层    # BaseProvider 接口 + ProviderRegistry

token 与成本统计:
  provider 返回 usage 为主
  tiktoken 或本地估算为辅

日志:
  logging
  JSONL 日志

API Key 存储:
  keyring 可选
  .env 仅用于开发环境
```

### 1.1 硬件需求

| 指标 | 估算 |
|---|---|
| 内存 | 轻量任务 500MB–1GB；大 PDF/大 Excel/多文件项目可达 1–2GB |
| 磁盘 | 程序 100–300MB；项目数据随 PDF、附件、缓存和日志增长 |
| CPU | 中低，PDF 解析和 Excel 读取会有短时 CPU 占用 |
| GPU | V1 不需要 |
| 网络 | LLM API、DOI 解析和在线资源发现需要网络 |

> 4GB 内存电脑可以运行 V1 轻量任务，推荐 8GB 以上内存获得更稳定体验。

### 1.2 扩展接口预留

```
BaseTableExtractor          # V1 实现 PdfPlumberExtractor / ExcelExtractor / CsvExtractor / ...
  ├── PdfPlumberExtractor
  ├── HtmlTableExtractor
  ├── ExcelExtractor
  ├── CsvExtractor
  ├── DocxExtractor
  └── ManualTableImporter

BrowserAdapter              # V1 不实现，V2 对公开网页和已登录会话再考虑 Playwright
```

---

## 2. 概念分离：开发工作包 vs 运行时 Agent

### 2.1 开发工作包 (Work Package)

| 工作包 | 名称 | 主要职责 | 优先级 |
|---|---|---|---|
| **WP0** | Specification & Fixtures | 样例数据、测试标准、字段规则样本 | 最高 |
| **WP1** | Foundation | 项目结构、数据库、配置、日志、LLM 多模型提供商系统 | 最高 |
| **WP2** | Ingestion | 论文导入、资源清单、PDF/附件管理 | 高 |
| **WP3** | Extraction | 表格读取、候选数据生成 | 高 |
| **WP4** | Schema & Unit Engine | 字段映射、单位换算、计算归档 | 最高 |
| **WP5** | Review & Memory | 人工审核、规则记忆、回滚 | 最高 |
| **WP6** | Export, Trace & Cost | 标准化导出、溯源、token 面板 | 中高 |
| **WP7** | UI | PySide6 桌面界面 | 中 |

### 2.2 软件运行时 Agent

| Runtime Agent | 是否必须使用 LLM | 说明 |
|---|---|---|
| Resource Finder | 部分需要 | 查找资源，生成资源清单 |
| Document Parser | 不一定 | 章节、表格、图片基础解析尽量规则化 |
| Relevance Judge | 需要 | 判断表格/附件是否相关 |
| Schema Mapper | 需要 + 规则 | 规则优先，LLM 辅助 |
| Unit Normalizer | 规则优先 | 单位换算必须确定性执行 |
| Review Manager | 不需要 | 生成审核任务和状态管理 |
| Memory Writer | 不需要 | 写入规则库 |
| Calculation Archivist | 不需要 | 保存公式和计算过程 |
| Cost Monitor | 不需要 | 统计 token 与费用 |

> 原则：能用确定性规则完成的部分，不要交给 LLM；只有语义判断、歧义解释和字段映射建议才调用 LLM。

---

## 3. 开发阶段

```
Phase 0 (第 0-3 天):   WP0 — 测试样例、schema、黄金规则
Phase 1 (第 1-2 周):   WP1 — 项目基础设施、数据库、CLI、日志
Phase 2 (第 2-4 周):   WP2 + WP3 — 文件导入、资源清单、候选表格
Phase 3 (第 4-6 周):   WP4 — 字段映射、单位换算、计算归档
Phase 4 (第 5-7 周):   WP5 — 人工审核、规则记忆、回滚
Phase 5 (第 6-8 周):   WP6 — 标准化导出、溯源、token 统计
Phase 6 (第 7-10 周):  WP7 — UI MVP
```

> 一人开发 10 周较紧但可执行；如需保证稳定性、UI 和多格式适配，实际更可能需要 12–16 周。

---

## WP0: Specification & Fixtures

**职责**: 准备样例、规则和验收标准。没有样例数据，后续每个模块都很难判断是否正确。

**依赖**: 无，最先启动

### Todo

- [x] 1. 准备 5 类测试文件：
  - [x] 1.1 简单 Excel 补充材料
  - [x] 1.2 多 sheet Excel
  - [x] 1.3 CSV 表格
  - [ ] 1.4 文本型 PDF 表格
  - [ ] 1.5 含 DOI/URL 的论文元数据样本
- [x] 2. 建立最小 geochem schema（Sample_ID, Latitude, Longitude, Formation, Lithology, SiO2, Al2O3, Na, Na2O, K, K2O, CIA, Reference, DOI）
- [x] 3. 建立 10 条黄金映射规则：
  - [x] 3.1 SiO₂ → SiO2（自动）
  - [x] 3.2 Na2O → Na2O（自动）
  - [x] 3.3 Na2O → Na 需要审核
  - [x] 3.4 K2O → K 需要审核
  - [x] 3.5 Chemical Index of Alteration → CIA
  - [x] 3.6 ppm → wt% 换算
  - [x] 3.7 mg/kg → ppm
  - [x] 3.8 FeOT 标记为高风险
  - [x] 3.9 LOI → LOI
  - [x] 3.10 Sample No. → Sample_ID
- [ ] 4. 准备标准输出样例（标准化 Excel/CSV）
- [ ] 5. 准备人工审核预期结果
- [ ] 6. 准备换算计算预期结果
- [ ] 7. 准备 token 统计样例
- [ ] 8. 写明验收标准：哪些字段必须自动识别、哪些必须进入审核、哪些换算必须生成计算档案

### 输出

```
tests/fixtures/
  schema_minimal.yaml
  article_sample.pdf
  table_s1_simple.xlsx
  table_s2_multisheet.xlsx
  geochem_sample.csv
  expected_mapping.yaml
  expected_standardized_output.xlsx
```

---

## WP1: Foundation

**职责**: 项目基础设施、数据库、配置、日志、LLM 多模型提供商系统。

**依赖**: WP0（需要样例 schema 和测试数据）

### Todo

- [x] 1. 初始化 Python 项目结构（pyproject.toml、src/、tests/）
- [x] 2. 实现 ProjectManager：新建/导入/切换项目、project.yaml 读写
- [x] 3. 实现 SchemaManager：YAML schema 解析、字段验证、别名管理、字段化学形态定义（element / oxide / index / isotope / metadata）
- [ ] 4. 实现 FileRegistry：文件 hash 计算、去重、路径管理
- [x] 5. 实现 SQLite 数据库 schema（17+ 张表，见下文数据库设计）
- [x] 6. 实现 JSONL 日志框架（基于 logging）
- [x] 7. 实现只读 MemoryStore：供 WP4 在映射前查询已知规则（WP5 后续补写入功能）
- [x] 8. 实现错误类型与异常处理
- [x] 9. 实现基础 CLI 命令：`new-project` / `import-schema` / `import-article` / `show-status`（import-article 待 WP2 实现）

### LLM 多模型提供商系统

这是 WP1 的核心子系统，负责统一管理所有大模型的调用。设计原则：**统一接口、多提供商、可配置、可扩展、可追踪**。

#### 架构

```
LLMClient（统一调用入口）
  ├── ProviderRegistry（提供商注册表）
  │     ├── OpenAIProvider
  │     ├── AnthropicProvider
  │     ├── GoogleProvider
  │     ├── DeepSeekProvider
  │     ├── MoonshotProvider
  │     ├── QwenProvider
  │     ├── OllamaProvider（本地模型）
  │     ├── LMStudioProvider（本地模型）
  │     └── CustomProvider（用户自定义 OpenAI 兼容接口）
  │
  ├── ModelConfig（模型配置）
  │     ├── 内置模型定价表
  │     ├── 用户自定义模型配置
  │     └── 每个任务可指定不同模型
  │
  ├── TokenTracker（token 统计）
  │     └── 每次调用写入 llm_calls 表
  │
  └── CostConfig（费用估算）
        └── 按模型/提供商计算费用
```

#### Todo — LLM 提供商核心

- [x] L1. 定义 BaseProvider 抽象接口：
  - `chat_completion(messages, model, **kwargs) -> LLMResponse`
  - ~~`stream_chat(messages, model, **kwargs) -> AsyncIterator`~~ （V1 暂缓）
  - `list_models() -> list[ModelInfo]`
  - `get_model_info(model_name) -> ModelInfo`
  - `validate_api_key() -> bool`
- [x] L2. 实现 ProviderRegistry：提供商注册、按名称查找、列出所有可用提供商
- [x] L3. 实现统一的 LLMResponse 数据结构

#### Todo — 内置提供商实现

- [x] L4. 实现 OpenAIProvider：
  - 支持 GPT-4o / GPT-4o-mini 等
  - 支持所有 OpenAI 兼容接口（DeepSeek、Moonshot、Qwen、OpenRouter 等 12 个提供商共用此类）
  - 支持自定义请求头（default_headers）
  - ~~流式输出~~ （V1 暂缓）
- [x] L5. 实现 AnthropicProvider：
  - 支持 Claude Opus / Sonnet / Haiku 系列
  - ~~extended thinking / 流式输出~~ （V1 暂缓）
- [ ] L6. 实现 GoogleProvider：（V1 暂缓，可通过 OpenRouter 使用 Gemini）
- [x] L7. DeepSeek 通过 OpenAIProvider 实现（OpenAI 兼容）
- [x] L8. Moonshot 通过 OpenAIProvider 实现（OpenAI 兼容）
- [x] L9. Qwen 通过 OpenAIProvider 实现（OpenAI 兼容）
- [x] L10. 实现 OllamaProvider（本地模型）：
  - 支持 Ollama 本地部署的所有模型
  - 自动检测本地 Ollama 服务是否运行
  - 支持模型列表查询
- [ ] L11. LMStudioProvider：（V1 暂缓，可通过 OpenAIProvider 自定义 base_url 实现）
- [x] L12. CustomProvider 通过 OpenAIProvider 实现：
  - 用户填写 API Base URL + API Key + 模型名
  - 兼容 OpenAI API 格式的任何第三方服务
  - 支持自定义请求头（default_headers）

**额外完成的提供商**（超出原计划）：
- [x] Xiaomi MiMo（OpenAI + Anthropic 双协议）
- [x] OpenRouter（免费模型聚合）
- [x] Zhipu GLM、MiniMax、Doubao、Baichuan、Hunyuan、Yi、StepFun

#### Todo — 模型配置与管理

- [x] L13. 实现 ModelConfig 数据模型：
  ```yaml
  providers:
    - name: anthropic
      api_key: sk-xxx        # 或环境变量引用
      base_url: https://api.anthropic.com
      models:
        - name: claude-sonnet-4-20250514
          display_name: Claude Sonnet 4
          max_tokens: 8192
          supports_streaming: true
          supports_vision: true
          cost_per_1k_input: 0.003
          cost_per_1k_output: 0.015

    - name: openai
      api_key: sk-xxx
      base_url: https://api.openai.com/v1
      models:
        - name: gpt-4o
          display_name: GPT-4o
          max_tokens: 16384
          supports_streaming: true
          supports_vision: true

    - name: deepseek
      api_key: sk-xxx
      base_url: https://api.deepseek.com/v1
      models:
        - name: deepseek-chat
          display_name: DeepSeek V3
        - name: deepseek-reasoner
          display_name: DeepSeek R1

    - name: ollama
      base_url: http://localhost:11434
      api_key: null
      models:
        - name: qwen2.5:14b
          display_name: Qwen 2.5 14B (Local)
        - name: llama3.1:8b
          display_name: LLaMA 3.1 8B (Local)

    - name: custom_1
      display_name: My Custom Provider
      api_key: sk-xxx
      base_url: https://my-api.example.com/v1
      api_format: openai_compatible
      models:
        - name: my-model-v1
          display_name: My Custom Model
  ```
- [x] L14. 实现模型定价配置表（内置 + 用户可覆盖）：
  - 内置主流模型的 input/output/cached token 价格
  - 用户可在配置文件中覆盖或补充价格
  - 支持免费模型（本地模型、免费 API）
- [x] L15. 实现 API Key 安全存储：
  - 配置文件中 API Key 字段支持 `${ENV_VAR}` 引用
  - ~~系统 keyring~~ （V1 暂缓，当前使用环境变量）
- [x] L16. 实现 API Key 连通性验证：
  - `geochem llm check` 命令：列出所有已配置提供商、验证 API Key、列出可用模型
  - 每个提供商独立验证，一个失败不影响其他

#### Todo — 任务级模型分配

- [x] L17. 实现任务-模型映射配置：
  ```yaml
  task_models:
    # 每个任务可以指定使用哪个提供商和模型
    field_mapping:        # 字段映射建议
      provider: anthropic
      model: claude-sonnet-4-20250514
      temperature: 0.1
      max_tokens: 4096

    relevance_judge:      # 相关性判断
      provider: openai
      model: gpt-4o-mini
      temperature: 0.0

    unit_suggestion:      # 单位换算建议
      provider: deepseek
      model: deepseek-chat
      temperature: 0.0

    # 未指定的任务使用默认模型
    _default:
      provider: anthropic
      model: claude-sonnet-4-20250514
  ```
- [x] L18. 实现模型回退机制：首选模型不可用时，自动尝试备选模型
  ```yaml
  fallback_chain:
    - provider: anthropic
      model: claude-sonnet-4-20250514
    - provider: openai
      model: gpt-4o
    - provider: deepseek
      model: deepseek-chat
  ```
- [x] L19. 实现 CLI 命令 `geochem llm test <task>`：用一个测试 prompt 跑指定任务的模型，验证端到端可用

#### Todo — 调用追踪与统计

- [x] L20. 实现 TokenTracker：每次 LLM 调用自动写入 llm_calls 表
- [x] L21. 实现 CostConfig：按模型定价和 token 用量估算费用
- [x] L22. 实现调用统计汇总：
  - 按提供商统计（调用次数、token 总量、费用）
  - 按模型统计
  - ~~按任务类型统计~~ （V1 暂缓）
  - ~~按文章统计~~ （V1 暂缓）
- [ ] L23. 实现异常检测：单次调用 token 超阈值、费用超阈值时告警
- [x] L24. 实现缓存策略：相同 prompt hash + 相同模型的结果缓存，避免重复调用

#### Todo — CLI 与配置管理

- [x] L25. 实现 CLI 命令 `geochem llm list`：列出所有已配置提供商和模型
- [ ] L26. 实现 CLI 命令 `geochem llm add-provider`：交互式添加新提供商（V1 暂缓，手动编辑 settings.yaml）
- [ ] L27. 实现 CLI 命令 `geochem llm set-default <task> <provider/model>`：设置任务默认模型（V1 暂缓）
- [x] L28. 实现 CLI 命令 `geochem llm cost-report`：查看当前项目的 LLM 费用报告（已实现为 `geochem llm cost`）

### 数据库表设计（17+ 张表）

```
projects              # 项目元数据
articles              # 论文元数据
resources             # 论文相关资源（PDF、Excel、附件等）
sections              # 论文章节结构
table_assets          # 原始表格资产（文件、sheet、页码）
candidate_tables      # 候选表格（统一结构）
candidate_columns     # 候选列（原始列名、单位候选、样本值）
candidate_rows        # 候选行（原始数据行）
field_mappings        # 字段映射建议
mapping_rules         # 映射规则库（确认后的规则）
review_items          # 审核任务
review_decisions      # 审核决策记录
calculation_records   # 计算过程记录
standardized_records  # 标准化数据记录
export_jobs           # 导出任务记录
llm_calls             # LLM 调用记录与 token 统计
processing_events     # 处理事件日志
```

### 验收

1. 能创建项目，读取 schema，写入数据库，产生日志
2. CLI 能执行 `new-project` → `import-schema` → `import-article` → `show-status` 完整流程
3. `geochem llm list` 能列出所有已配置模型
4. `geochem llm check` 能验证 API Key 连通性
5. 至少两个不同提供商（如 Anthropic + OpenAI）能成功调用
6. 任务级模型分配生效（field_mapping 用模型 A，relevance_judge 用模型 B）
7. 每次调用都有 llm_calls 记录，包含 provider、model、token 用量、费用

---

## WP2: Document Ingestion

**职责**: 论文和补充材料导入，生成资源清单。

**依赖**: WP1（需要项目结构、数据库）

**核心思路**: 用户手动导入文件或提供 DOI/链接。系统不自动爬取，而是弹出系统浏览器让用户完成登录和下载。

### Todo

- [ ] 1. 实现文件导入命令：用户选择本地 PDF/Excel/CSV/DOCX/ZIP 文件导入项目
- [ ] 2. 实现文件复制到项目目录（raw/articles/, raw/supplementary/）
- [ ] 3. 实现文件 hash 计算与去重
- [ ] 4. 实现资源类型自动判断（main_pdf / supplementary_excel / csv / docx / zip / figure）
- [ ] 5. 实现 Resource 表写入
- [ ] 6. 实现 DOI 元数据解析（通过 CrossRef API 提取标题、作者、年份、期刊）
- [ ] 7. 实现 URL 弹出系统浏览器（`webbrowser.open()`），用户手动下载后导入
- [ ] 8. 实现 PDF 元数据提取（PyMuPDF：标题、作者、页数）
- [ ] 9. 实现 Articles 表写入

### 浏览器 + LLM 在线读取策略

**场景 A：用户只给 DOI，没有 PDF**

1. 用户输入 DOI → app 调用 `webbrowser.open()` 打开论文页面
2. 用户在系统浏览器中完成机构登录、人机验证
3. 用户回到 app，确认"已登录完成"
4. App 调用 LLM（WebFetch/直接请求）读取已认证的网页内容
5. LLM 从网页中提取论文正文和表格数据
6. 数据直接进入 WP3 抽取流程，无需下载 PDF

**场景 B：用户已有本地文件**

1. 用户直接导入 PDF/Excel/CSV 文件
2. 进入 WP3 本地处理流程

### 实现要点

- [ ] 10. 实现 DOI → URL 解析（CrossRef API 或 doi.org 重定向）
- [ ] 11. 实现浏览器弹出 + 用户确认回调（等待用户确认"已登录"）
- [ ] 12. 实现 LLM 网页读取（WebFetch 或 requests + cookies）
- [ ] 13. 实现网页内容 → LLM 抽取流程（与 WP3 衔接）

### 验收

用户输入 DOI → 弹出浏览器 → 用户登录 → 回到 app → LLM 能读取网页内容并提取论文数据。

---

## WP3: LLM-Driven Data Extraction

**职责**: 从导入的文件中抽取与 schema 表头对应的数值数据。

**依赖**: WP1（SchemaManager、LLMClient）、WP2（资源清单）

**核心思路**: 传统库负责"读文件"，LLM 负责"理解内容并抽取结构化数据"。不写复杂的传统解析器，把理解表格的活交给大模型。

### 两条路径

```
路径 A（用户给 DOI/链接，无本地文件）:
  用户输入 DOI → 弹出浏览器 → 用户完成登录/验证 → 回到 app →
  LLM 直接读取网页内容 → LLM 抽取结构化数据

路径 B（用户给本地文件）:
  用户导入 PDF/Excel/CSV → 库读取原始内容 → LLM 抽取结构化数据
```

### 文件读取层（传统库）

- [ ] 1. 实现 ExcelReader：基于 openpyxl，读取所有 sheet 的表格数据
- [ ] 2. 实现 CsvReader：CSV/TXT 读取
- [ ] 3. 实现 PdfReader：基于 PyMuPDF/pdfplumber，提取文本和表格
- [ ] 4. 实现 DocxReader：基于 python-docx
- [ ] 5. 实现 ZIP 安全解压（防止路径穿越）

### LLM 抽取层

- [ ] 6. 实现 LLMExtractor 核心逻辑：
  - 输入：文件原始内容（文本/表格）+ schema 表头列表
  - 输出：结构化的候选数据（每行对应一个样品，每列对应 schema 字段）
  - prompt 模板：告诉 LLM "以下是论文表格数据，请按以下表头抽取，保留原始值和单位"
- [ ] 7. 实现 Excel → LLM 抽取流程：
  - openpyxl 读取 sheet → 转为文本/markdown 表格 → 发送给 LLM → LLM 返回结构化 JSON
- [ ] 8. 实现 PDF → LLM 抽取流程：
  - PyMuPDF/pdfplumber 提取文本和表格 → 发送给 LLM → LLM 返回结构化 JSON
- [ ] 9. 实现抽取结果写入 candidate_tables / candidate_columns / candidate_rows
- [ ] 10. 实现抽取置信度记录
- [ ] 11. 实现抽取失败原因记录

### LLM Prompt 设计

```
系统 prompt：
  你是一个地球化学数据抽取助手。用户会给你论文中的表格数据，
  你需要按照给定的 schema 表头抽取每个样品的数据。

用户 prompt：
  以下是论文 "{title}" 中的表格数据：
  {table_content}

  请按照以下 schema 字段抽取：
  {schema_fields}

  对于每个样品，返回 JSON 格式：
  [
    {"SampleID": "...", "SiO2": 71.08, "SiO2_unit": "wt%", "Al2O3": 15.0, ...},
    ...
  ]

  注意：
  1. 保留原始数值，不做单位转换
  2. 如果某个字段在表格中找不到，填 null
  3. 记录每个字段的原始单位
  4. 表头可能使用别名或缩写，如 SiO₂ 即 SiO2
```

### 输出结构

```json
{
  "table_id": "TBL_001",
  "article_id": "ART_001",
  "resource_id": "RES_002",
  "source_type": "excel",
  "extract_method": "llm",
  "schema_fields_matched": ["SampleID", "SiO2", "Al2O3", ...],
  "rows": [
    {"SampleID": "XD2P-B26", "SiO2": 20.79, "SiO2_unit": "wt%", ...},
    ...
  ],
  "confidence": 0.85,
  "llm_call_id": "LLM_000042"
}
```

### 验收

用 xinmen2022_ordovician.xlsx 测试：LLM 能从表格中抽取 21 行数据，与原表逐列对比一致。

---

## WP4: Schema & Unit Engine（LLM 辅助）

**职责**: 字段映射、单位识别、化学形态判断、计算归档。

**依赖**: WP1（SchemaManager、MemoryStore、LLMClient）、WP3（候选数据）

**核心思路**: 规则匹配优先（快速、确定性），LLM 处理不确定项（灵活、智能）。

### 规则层（确定性）

- [ ] 1. 实现 schema 别名精确匹配（已由 SchemaManager.match_field 完成）
- [ ] 2. 实现 Unicode 归一化（SiO₂ → SiO2，已由 normalize_field_name 完成）
- [ ] 3. 实现 memory 规则匹配（查询已确认的映射规则）
- [ ] 4. 实现换算因子库（YAML 配置：原子量、分子量、换算公式）
- [ ] 5. 实现确定性换算：
  - [ ] 5.1 ppm ↔ wt%
  - [ ] 5.2 mg/kg ↔ ppm
  - [ ] 5.3 μg/g ↔ ppm
  - [ ] 5.4 oxide ↔ element（Na2O→Na、K2O→K、CaO→Ca 等）
  - [ ] 5.5 百分比 ↔ 小数
- [ ] 6. 实现 CIA 计算（检查 Al2O3、CaO*、Na2O、K2O 是否存在且单位一致）

### LLM 层（处理不确定项）

- [ ] 7. 实现 LLM 映射建议：规则匹配不到的字段，发给 LLM 判断
  - prompt：给定源字段名 + 样本值 + schema 候选列表，让 LLM 判断最可能的映射
- [ ] 8. 实现 LLM 单位识别：给定字段名 + 数值，让 LLM 判断单位和是否需要转换
- [ ] 9. 实现风险等级自动划分：
  - 直接同名 → 低风险（自动）
  - 别名匹配 → 低风险（自动）
  - 单位换算 → 中风险（需确认）
  - 氧化物↔元素 → 高风险（首次必须审核）
  - LLM 建议 → 高风险（必须审核）

### 归档

- [ ] 10. 实现计算过程 Markdown 档案生成
- [ ] 11. 实现行级 calculation_records.jsonl
- [ ] 12. 实现换算公式注册机制：支持用户自定义公式

### 验收

Na2O → Na 高风险标记正确，CIA 计算正确，LLM 能识别 "Chemical Index of Alteration" → CIA。

### 字段风险等级

| 类型 | 示例 | 默认处理 |
|---|---|---|
| 直接同名 | SiO2 → SiO2 | 自动 |
| 符号规范化 | SiO₂ → SiO2 | 自动 |
| 简单别名 | Chemical Index of Alteration → CIA | 中风险，建议审核一次 |
| 单位换算 | ppm → wt% | 中风险，需确认单位维度 |
| 氧化物转元素 | Na2O → Na | 高风险，首次必须审核 |
| 总铁相关 | FeOT、TFe、FeO* | 高风险，必须审核 |
| 公式指标 | CIA、WIP | 高风险，必须保存公式和输入字段 |

### 关键原则

1. Na2O → Na 默认不能静默换算，必须审核一次
2. FeO、Fe2O3、FeOT、TFe 必须标记高风险
3. CIA 计算必须检查 Al2O3、CaO、Na2O、K2O 是否存在且单位一致
4. 如果 CaO 是否需要校正不明确，则 CIA 计算进入审核

### 验收

系统能够对 Na2O、K2O、SiO₂、CIA、FeOT 等字段给出正确风险等级。

---

## WP5: Review & Memory（Agent 模式）

**职责**: 人工审核决策 + Agent 自动学习和记忆规则。

**依赖**: WP1（数据库、MemoryStore）、WP4（映射建议和置信度）

**核心思路**: 人工审核是人做的，但规则记忆是 Agent 自动完成的。用户确认一次 Na2O→Na 后，Agent 自动将这条规则写入 MemoryStore，下次遇到相同条件自动应用。

### 人工审核部分

- [ ] 1. 实现审核队列生成（高风险 > 中风险 > 低风险排序）
- [ ] 2. 实现审核操作处理：
  - [ ] 2.1 接受 AI 建议
  - [ ] 2.2 修改目标字段
  - [ ] 2.3 修改单位
  - [ ] 2.4 选择公式
  - [ ] 2.5 跳过字段
  - [ ] 2.6 新建字段
  - [ ] 2.7 标记为以后再处理
- [ ] 3. 实现批量操作：批量接受低风险映射
- [ ] 4. 实现原始表格片段展示（给用户看上下文）

### Agent 规则记忆部分

- [ ] 5. 实现 RuleMemoryAgent：用户审核决策后自动写入规则
  - 触发时机：用户确认/修改映射时
  - 写入内容：源字段 → 目标字段、单位、换算公式、风险等级、审核状态
  - 作用域：global（全局）/ project（项目级）
- [ ] 6. 实现 mapping_rules.yaml 自动写入
- [ ] 7. 实现 mapping_memory.md 同步（人类可读版本）
- [ ] 8. 实现规则版本号和历史追踪
- [ ] 9. 将只读 MemoryStore 升级为可读写 MemoryStore
- [ ] 10. 实现规则禁用与回滚
- [ ] 11. 实现规则复用：新文章导入时，Agent 自动查询 MemoryStore 已有规则，减少重复审核

### 验收

用户确认一次 Na2O→Na 的处理方式后，Agent 自动写入规则，下一篇文章遇到相同条件时自动应用，不再需要审核。

---

## WP6: Export, Trace & Cost

**职责**: 标准化导出、溯源报告、token 成本统计。

**依赖**: WP1（数据库）、WP4（标准化数据）、WP5（审核状态）

### Todo

- [ ] 1. 实现标准化 Excel 导出（openpyxl）
- [ ] 2. 实现标准化 CSV 导出
- [ ] 3. 实现 SQLite 数据写入
- [ ] 4. 实现标准输出字段追加（Reference、DOI、Source_File、Source_Table、Source_Row、Original_Field、Original_Unit、Original_Value、Mapped_Field、Mapped_Unit、Stored_Value、Mapping_Rule_ID、Calculation_ID、Review_Status、Confidence、Processed_At）
- [ ] 5. 实现数据质量等级标记（A/B/C/D/E）
- [ ] 6. 实现 CalculationRecord 归档
- [ ] 7. 实现单条溯源查询
- [ ] 8. 实现文章级处理报告
- [ ] 9. 实现项目级成本报告
- [ ] 10. 实现 LLM 调用明细导出
- [ ] 11. 实现异常 token 消耗标记
- [ ] 12. 实现导出任务记录
- [ ] 13. 编写导出格式验证测试

### llm_calls 表字段

```
call_id, project_id, article_id, agent_name, skill_name,
model_provider, model_name, prompt_version, prompt_hash,
input_tokens, output_tokens, cached_tokens, total_tokens,
estimated_cost, started_at, ended_at, latency_ms,
status, error_message, retry_count,
request_summary, response_summary
```

### 验收

任意一个导出的 Na 值，都可以查到：原始字段、原始值、原始单位、换算公式、计算结果、审核记录、规则 ID 和来源表格。

---

## WP7: UI MVP

**职责**: PySide6 桌面界面。

**依赖**: 所有 WP 的数据模型稳定后推进

### Todo

- [ ] 1. 搭建 PySide6 应用框架（主窗口、导航栏、状态栏）
- [ ] 2. 实现项目页面：项目列表、进度概览
- [ ] 3. 实现 Schema 配置页面：Excel 表头上传、字段编辑、预览和保存
- [ ] 4. 实现文件导入页面：DOI/URL 输入 + "在浏览器中打开" 按钮、PDF/Excel/CSV 上传（支持拖拽）
- [ ] 5. 实现资源清单页面：资源列表、状态展示
- [ ] 6. 实现候选表格页面：表格预览、列信息展示
- [ ] 7. 实现字段映射页面：映射建议列表、置信度展示
- [ ] 8. 实现审核页面：按风险分组的待审核列表、原始表格片段、AI 建议、目标字段选择器、换算规则选择器、批量操作
- [ ] 9. 实现导出页面：格式选择、导出执行、导出记录
- [ ] 10. 实现溯源页面：搜索框（样品/字段）、溯源链展示、原始数据/计算过程/AI 理由/审核记录
- [ ] 11. 实现 token 消耗页面：项目/文章/Agent/模型维度统计、费用估算、异常提醒
- [ ] 12. 实现 LLM 配置页面：
  - [ ] 12.1 提供商列表：显示已配置的提供商、连接状态、可用模型数
  - [ ] 12.2 添加提供商：选择提供商类型、填写 API Key 和 Base URL、验证连通性
  - [ ] 12.3 自定义提供商：填写名称、API Base URL、API Key、选择 OpenAI 兼容格式
  - [ ] 12.4 模型列表：每个提供商下的模型、显示名称、最大 token、支持能力（streaming/vision）
  - [ ] 12.5 任务模型分配：为每个任务（字段映射、相关性判断等）选择默认模型
  - [ ] 12.6 回退链配置：设置模型不可用时的自动回退顺序
  - [ ] 12.7 模型定价编辑：查看/修改每个模型的 input/output token 价格
  - [ ] 12.8 本地模型管理：检测 Ollama/LM Studio 服务状态、列出本地可用模型
  - [ ] 12.9 连通性测试：一键测试所有已配置提供商的 API Key
  - [ ] 12.10 LLM 调用日志：查看最近的调用记录、token 用量、费用、延迟
- [ ] 13. 实现主题样式和交互优化
- [ ] 14. 编写 UI 自动化测试（pytest-qt）

### UI 实现注意

1. PDF 解析、Excel 读取、LLM 调用必须放到后台线程，不能阻塞主界面
2. 每个长任务要有进度状态
3. 每个任务要支持取消
4. 错误提示必须能定位到具体文件和步骤
5. 审核页面优先做清楚，不要追求复杂视觉效果

---

## 8. 接口约定

### 8.1 核心数据对象

#### Article

```json
{
  "article_id": "ART_001",
  "title": "...",
  "authors": ["..."],
  "year": 2020,
  "doi": "10.xxxx/xxxxx",
  "url": "https://...",
  "status": "imported"
}
```

#### Resource

```json
{
  "resource_id": "RES_001",
  "article_id": "ART_001",
  "resource_type": "supplementary_excel",
  "file_name": "Table_S1.xlsx",
  "local_path": "raw/supplementary/Table_S1.xlsx",
  "source_url": "https://...",
  "file_hash": "...",
  "status": "parsed"
}
```

#### CandidateColumn

```json
{
  "column_id": "COL_001",
  "table_id": "TBL_001",
  "raw_name": "Na2O",
  "normalized_name": "Na2O",
  "unit_candidate": "wt%",
  "sample_values": [2.31, 2.45, 2.18]
}
```

#### FieldMappingSuggestion

```json
{
  "mapping_id": "MAP_SUG_001",
  "source_field": "Na2O",
  "target_field": "Na",
  "source_unit": "wt%",
  "target_unit": "wt%",
  "mapping_type": "oxide_to_element",
  "confidence": 0.78,
  "risk_level": "high",
  "requires_review": true,
  "reason": "Target field is elemental Na, source field is Na2O oxide. Conversion required."
}
```

#### CalculationRecord

```json
{
  "calc_id": "CALC_001",
  "article_id": "ART_001",
  "table_id": "TBL_001",
  "row_id": "ROW_001",
  "target_field": "Na",
  "source_field": "Na2O",
  "source_value": 2.87,
  "source_unit": "wt%",
  "formula": "Na = Na2O * 0.741857",
  "result": 2.129,
  "target_unit": "wt%",
  "rule_id": "RULE_001",
  "review_status": "confirmed",
  "archive_file": "calculations/ART_001/CALC_001.md"
}
```

#### ReviewItem

```json
{
  "review_id": "REV_001",
  "item_type": "field_mapping",
  "risk_level": "high",
  "original_field": "Na2O",
  "original_unit": "wt%",
  "ai_suggestion": "Convert Na2O to elemental Na",
  "confidence": 0.78,
  "available_actions": ["accept", "change_target", "new_field", "skip", "save_rule"]
}
```

### 8.2 数据流方向

```
WP1 (Foundation)
  ├── 提供: ProjectManager, SchemaManager, FileRegistry, LLMClient(多提供商), ProviderRegistry, TokenTracker, CostConfig, SQLite DB, MemoryStore
  │
WP2 (Ingestion)
  ├── 输入: project_id, article_source (DOI/URL/PDF/本地文件路径)
  ├── 输出: article_id, resource_inventory.csv
  ├── 浏览器策略: webbrowser.open() 弹出系统浏览器，用户手动操作后导入文件
  │
WP3 (Extraction)
  ├── 输入: resource_inventory, schema
  ├── 输出: candidate_tables, candidate_columns, candidate_rows
  │
WP4 (Schema & Unit Engine)
  ├── 输入: candidate_tables/columns/rows, schema, mapping_rules (from MemoryStore)
  ├── 输出: field_mapping_suggestions, calculation_records, standardized_records (draft)
  │
WP5 (Review & Memory)
  ├── 输入: field_mapping_suggestions, confidence_scores
  ├── 输出: review_decisions, mapping_rules.yaml, mapping_memory.md
  │
WP6 (Export, Trace & Cost)
  ├── 输入: standardized_records (confirmed), calculation_records, review_decisions, llm_calls
  ├── 输出: Excel/CSV/SQLite, traceability_reports, cost_reports
  │
WP7 (UI)
  ├── 调用: 所有 WP 的公开接口
  ├── 展示: 各阶段结果和统计数据
```

---

## 9. 关键风险与应对

### 9.1 PDF 表格抽取风险

**问题**: pdfplumber 对复杂表格、跨页表格、多级表头和扫描 PDF 不稳定。

**应对**: V1 不承诺完全自动抽取所有 PDF 表格。应提供人工导入 Excel/CSV 和手动修正入口。

### 9.2 期刊网页访问风险

**问题**: requests 无法处理大量动态网页、登录态和反爬限制。

**应对**: V1 主路径为用户手动下载文件后导入。Resource Finder 只作为辅助功能。

### 9.3 字段映射风险

**问题**: Na 与 Na2O、FeO 与 Fe2O3、FeOT 与 TFe 等不能简单映射。

**应对**: Schema 中必须定义字段的化学形态：element / oxide / index / isotope / metadata。

### 9.4 公式计算风险

**问题**: CIA 等指标公式可能涉及 CaO 校正或不同表达方式。

**应对**: 所有指标计算必须记录公式版本、输入字段和用户确认状态。

### 9.5 token 成本风险

**问题**: 如果每个表格、每个字段都调用 LLM，成本会迅速升高。

**应对**: 规则优先；LLM 只处理不确定项；相同字段和相同上下文使用缓存。

### 9.6 LLM 提供商风险

**问题**: 不同提供商 API 格式差异大、模型更新频繁、API Key 可能过期、国内访问部分服务需要代理。

**应对**:
1. 统一 BaseProvider 接口，每个提供商独立实现，互不影响
2. 内置模型配置可随时更新，用户也能手动补充新模型
3. API Key 连通性验证（`geochem llm check`）提前发现问题
4. 模型回退链机制：首选模型不可用时自动尝试备选
5. 本地模型（Ollama / LM Studio）作为离线备选方案

### 9.7 UI 过早复杂化风险

**问题**: 数据模型未稳定前做完整 UI，后期会反复返工。

**应对**: 先 CLI 跑通流程，UI 从审核和导出两个关键页面开始。

---

## 10. MVP 验收标准

### 10.1 功能验收

1. 能创建项目并导入 schema
2. 能导入一篇 PDF 和一个 Excel 补充材料
3. 能生成资源清单
4. 能读取 Excel 多 sheet
5. 能生成候选表格
6. 能识别常见字段并给出映射建议
7. 能将 Na2O→Na 标为高风险审核项
8. 用户确认后能写入 mapping_rules.yaml
9. 能执行一次 Na2O→Na 换算
10. 能生成计算 Markdown 档案
11. 能导出标准化 Excel/CSV
12. 能查看单条数据溯源
13. 能统计 LLM 调用 token
14. 能配置至少 2 个不同 LLM 提供商（如 Anthropic + OpenAI）
15. 能为不同任务指定不同模型（字段映射用模型 A，相关性判断用模型 B）
16. 能添加自定义 OpenAI 兼容提供商（填写 API Base URL + Key）
17. 能连接本地模型（Ollama / LM Studio）
18. `geochem llm check` 能验证所有已配置提供商的连通性
19. 模型不可用时能自动回退到备选模型

### 10.2 数据验收

1. 所有正式导出数据必须有 Source_File
2. 所有换算数据必须有 Calculation_ID
3. 所有高风险映射必须有 Review_Status
4. 未确认高风险数据不得进入正式导出
5. 每个 LLM 调用必须有 llm_calls 记录

### 10.3 工程验收

1. 每个模块有单元测试
2. 至少有一个端到端测试
3. 所有中间结果可复现
4. 任务失败后可以从上一步恢复
5. 日志能定位错误文件和错误模块
