# GeoChem Data Curation Agent 阶段性交付文档

版本：V1 CLI 闭环 + Agent 进化闭环 + WP6 Export/Trace/Cost  
日期：2026-05-29  
阶段定位：可审核、可追溯、可学习、可导出的科研地化数据整理 CLI 原型，为后续 UI 设计提供功能边界与信息架构基础。

---

## 1. 阶段性结论

当前系统已经从早期的“LLM 单次表头映射实验”推进到一个较完整的 CLI 工作流：

```text
项目创建
→ 论文/补充材料导入
→ Excel/CSV/PDF/HTML 资源读取
→ 完整候选表保存
→ 用户目标表头建模
→ 规则优先字段映射
→ 分组 LLM 补缺
→ 人工审核
→ 映射记忆
→ 单位/化学形态换算
→ 用户示教补值
→ Agent 学习规则
→ 标准化记录生成
→ 行级追溯
→ 成本统计
→ 数据 + 审计包导出
```

现阶段最重要的产品能力是：

1. **用户表头驱动**：标准化输出优先遵循用户 Excel 原始表头、顺序和单位。
2. **候选数据完整保存**：原始表格行列先完整落库，不依赖 LLM 直接生成最终表。
3. **规则优先、LLM 补缺**：确定性规则、schema alias、normalization、memory 优先；LLM 只处理未确定字段。
4. **人工审核闭环**：高风险映射、单位不一致、低置信规则进入 review。
5. **Agent 进化**：用户指出漏值或漏行来源后，系统保存证据、补值、学习规则并复用。
6. **可入库治理**：标准化记录包含来源、原始值、映射、审核、计算、质量等级等治理信息。
7. **WP6 审计能力**：支持行级 trace、LLM 成本聚合和数据审计包导出。

当前测试状态：

```bash
.venv/bin/python -m pytest tests/unit -q
```

结果：`169 passed, 5 warnings`

---

## 2. 当前功能总览

### 2.1 项目与配置管理

系统支持以项目为单位组织论文、资源、schema、memory、标准化输出和审计报告。

已具备能力：

- 创建项目：`geochem new-project`
- 查看项目：`geochem list-projects`
- 查看项目状态：`geochem status`
- 导入 schema：`geochem import-schema`
- 项目目录自动创建：
  - `schema/`
  - `raw/articles/`
  - `raw/supplementary/`
  - `candidate_data/`
  - `review/`
  - `memory/`
  - `calculations/`
  - `output/`
  - `reports/`
- SQLite 数据库自动初始化。
- 旧项目打开时会自动补齐新增 schema 列。
- API key 支持 `${ENV_VAR}` 环境变量占位，当前小米 MiMo 使用 `MIMO_API_KEY`。

UI 启示：

- 需要一个“项目列表页”。
- 需要一个“项目概览 Dashboard”，展示文章数、资源数、候选表数、待审核项、规则数、LLM 调用数、导出次数。
- 项目设置页需要显示 schema、memory、默认导出路径、LLM provider 状态。

---

### 2.2 LLM Provider 与成本记录

系统支持多 provider LLM，并统一记录调用信息。

已具备能力：

- 查看 provider：`geochem llm list`
- 检查 provider：`geochem llm check`
- 测试模型：`geochem llm test`
- 成本统计：`geochem llm cost`
- 成本聚合维度：
  - `model`
  - `task`
  - `agent`
  - `article`
  - `provider`
- 每次 LLM 调用写入 `llm_calls`：
  - project/article
  - agent/skill
  - provider/model
  - prompt hash
  - tokens
  - latency
  - estimated cost
  - request/response summary
  - status/error

当前策略：

- 不做预算拦截。
- 只做统计、审计和导出。
- LLM prompt 避免发送完整数据行，优先发送表头、描述、单位、文章上下文和用户示教证据。

UI 启示：

- 需要“LLM 设置页”：provider 状态、模型选择、环境变量状态。
- 需要“成本页”：按模型/任务/文章聚合，可筛选、可导出。
- 需要“调用详情抽屉”：展示 task、token、latency、request summary、response summary。

---

### 2.3 文件与论文导入

系统支持本地文件和 DOI 作为入口。

已具备能力：

- 导入本地文件：`geochem import-file`
- 导入 DOI：`geochem import-doi`
- 支持资源类型：
  - `main_pdf`
  - `supplementary_pdf`
  - `supplementary_csv`
  - `supplementary_excel`
  - `html_page`
  - 其他补充材料类型保留接口
- 资源写入 `resources`：
  - resource type
  - file name
  - local path
  - source URL
  - file hash
  - file size
  - status

UI 启示：

- 需要“文章详情页”。
- 文章详情页下需要“资源列表”：
  - 文件名
  - 类型
  - 状态
  - 来源
  - 操作：抽取、查看候选表、重新导入。

---

### 2.4 表格抽取与候选数据保存

系统现在采用 reader-first 策略，先完整保存候选表，再做映射。

已具备能力：

- Excel/CSV 表格读取。
- PDF 初步表格读取。
- HTML 资源接口。
- 抽取命令：`geochem extract`
- 默认策略：
  - 对 Excel/CSV 等结构化资源，优先完整读取原始表。
  - 不默认把整张数据表交给 LLM。
  - `--llm-map` 可走旧的 LLM 映射抽取链路。
- 候选表落库：
  - `candidate_tables`
  - `candidate_columns`
  - `candidate_rows`
- 候选列保存：
  - raw name
  - normalized name
  - unit candidate
  - dtype
  - sample values
- 候选行保存完整 raw JSON。

UI 启示：

- 需要“候选表浏览器”：
  - 左侧资源/sheet/table 列表。
  - 中间表格预览。
  - 右侧列信息：原始表头、识别单位、样例值、映射状态。
- 候选表页应支持导出 candidate rows，用于调试。

---

### 2.5 用户目标表头模型

系统已经建立“用户目标表头”概念，解决标准化输出必须和用户 Excel 表头一致的问题。

已具备能力：

- 从 Excel 第一行读取目标表头、单位、顺序。
- 从 `headers.json` 读取中文字段描述。
- 合并为 `TargetHeader`：
  - `display_header`
  - `source_header`
  - `canonical_field`
  - `description`
  - `target_unit`
  - `field_group`
- 导出时按用户 display header 输出。
- 缺数据列保留为空，不删除。
- 支持特殊表头规范化：
  - `δ15Nbulk\n‰`
  - `Li\nppm`
  - `Age（min）`
  - `Depth/m`
  - `Fepy/Fehr`
  - `C/Nmol`
  - `Dry density\ng/cm3`
- 字段分组：
  - `basic_info`
  - `location`
  - `stratigraphy_age`
  - `sample_context`
  - `isotope_organic`
  - `major_elements`
  - `trace_elements`
  - `ree`
  - `iron_speciation`
  - `weathering_indices`

UI 启示：

- 需要“目标表头配置页”：
  - 展示用户原始表头。
  - 展示字段描述。
  - 展示单位。
  - 展示字段分组。
  - 标记哪些字段已映射、缺值、高风险。
- UI 中字段分组应作为一级筛选或 Tab。

---

### 2.6 表头规范化与字段映射

映射引擎已经从“单次 LLM 映射 156 字段”升级为规则优先。

已具备能力：

- 映射命令：`geochem map`
- 支持参数：
  - `--grouped`
  - `--header-descriptions`
  - `--provider`
  - `--model`
  - `--no-llm`
- 映射优先级：
  1. schema exact/alias
  2. HeaderNormalizer
  3. MemoryStore
  4. grouped LLM fallback
- LLM fallback 按字段类型分组。
- 已确定字段不再发给 LLM。
- LLM prompt 只包含：
  - 原始表头
  - 目标表头
  - 目标单位
  - 字段描述
  - 必要文章上下文
- 映射结果写入 `field_mappings`：
  - source field
  - target field
  - source unit
  - target unit
  - mapping type
  - confidence
  - risk level
  - requires review
  - reason

UI 启示：

- 需要“字段映射工作台”：
  - 按字段分组查看。
  - 原始列与目标列并排。
  - 显示单位、置信度、风险、原因。
  - 支持用户改 target field/unit。
  - 支持保存为规则。

---

### 2.7 人工审核闭环

系统已经支持 review queue，用于处理不确定映射、换算和 learned rule。

已具备能力：

- 查看审核项：`geochem review list`
- 决策审核项：`geochem review decide`
- 支持 action：
  - `accept`
  - `reject`
  - `edit`
  - `defer`
- 可保存规则：
  - 写入 `mapping_rules`
  - 写入 `memory/mapping_rules.yaml`
  - 写入 `memory/mapping_memory.md`
- learned rule 低置信或中高风险时自动进入 review。
- accept learned rule 后，`learned_extraction_rules.review_status` 变为 confirmed。

UI 启示：

- 需要“审核队列页”：
  - 风险等级筛选。
  - item type 筛选：field mapping、unit conversion、learned rule、patch。
  - 显示原始字段、单位、样例值、AI 建议、证据、原因。
  - 操作按钮：接受、拒绝、编辑、延后、保存为规则。
- 需要“审核详情弹窗/抽屉”。

---

### 2.8 单位换算与计算归档

系统支持第一批确定性换算，并且保存计算档案。

已具备能力：

- 支持换算：
  - `ppm -> wt%`
  - `mg/kg -> ppm`
  - `ug/g -> ppm`
  - `μg/g -> ppm`
  - `Na2O -> Na`
  - `K2O -> K`
- 换算写入 `calculation_records`。
- 行级计算档案写入：
  - `calculations/<article_id>/row_level_calculations.jsonl`
- 标准化记录保存 calculation IDs。
- 涉及氧化物/元素换算时风险较高，需要 review 策略配合。

UI 启示：

- 标准化表格中应能展开“计算详情”。
- 计算详情应显示：
  - 原始值
  - 原始单位
  - 目标单位
  - 公式
  - substitution
  - result
  - rule/review status

---

### 2.9 标准化数据生成

系统已经能从候选数据、映射、审核和 patch 生成 `standardized_records`。

已具备能力：

- 标准化命令：`geochem standardize`
- 生成 `standardized_records`。
- 保留治理字段：
  - source file
  - source table
  - source row
  - original fields
  - original units
  - original values
  - mapped fields
  - mapped units
  - mapping rule IDs
  - calculation IDs
  - review statuses
  - confidence scores
  - quality grade
  - processed at
- 保守入库策略：
  - 未审核高风险映射不进入正式标准化。
  - A/B/C 可作为正式导出数据。
  - D/E 留作候选或跳过。
- 标准化过程幂等：
  - 同一 table 重新 standardize 会清理旧 standardized/calculation 结果后重建。

UI 启示：

- 需要“标准化结果表”：
  - 类似数据库表格。
  - 支持按质量等级筛选。
  - 支持按字段缺失率筛选。
  - 支持点击某一行查看 trace。

---

## 3. Agent 进化闭环

### 3.1 设计目标

当用户发现 LLM 没有找到某些行或某些列值时，不要求用户每次都手动修表，而是让用户告诉 agent：

> 数据在文章/附录/表格的这里。

系统需要完成：

1. 保存这次用户示教。
2. 用示教补全当前结果。
3. 从多条示教中归纳可复用规则。
4. 后续遇到相似文章或相似表头时复用规则。
5. 所有补值保留证据链。

### 3.2 已实现数据层

新增表：

| 表名 | 用途 |
|---|---|
| `teaching_events` | 用户示教事件，保存字段、样品、值、单位、证据、来源位置 |
| `learned_extraction_rules` | 从示教中学习出的规则 |
| `record_patches` | 用户示教或规则复用产生的补值 |

关键原则：

- 不修改 `candidate_rows`。
- patch 作为叠加层进入标准化。
- candidate 原始值优先，patch 只补空值。
- patch-only 缺失行也能生成标准化记录。

### 3.3 已实现 CLI

添加示教：

```bash
geochem teach add \
  --project ARTICLE_TEST_001 \
  --article ARTICLE_TEST_001 \
  --table TBL_005 \
  --sample-id XM-01 \
  --target-header "Li ppm" \
  --target-field Li \
  --unit ppm \
  --value 42.1 \
  --source appendix \
  --evidence "Supplementary Table S1, column Li(ppm), row sample XM-01"
```

查看示教：

```bash
geochem teach list --project ARTICLE_TEST_001 --article ARTICLE_TEST_001
```

学习规则：

```bash
geochem teach learn \
  --project ARTICLE_TEST_001 \
  --article ARTICLE_TEST_001 \
  --provider xiaomi \
  --model mimo-v2.5-pro
```

不调用 LLM、本地归纳：

```bash
geochem teach learn \
  --project ARTICLE_TEST_001 \
  --article ARTICLE_TEST_001 \
  --no-llm
```

应用规则：

```bash
geochem teach apply --project ARTICLE_TEST_001 --table TBL_005
```

解释 patch：

```bash
geochem teach explain --project ARTICLE_TEST_001 --patch PATCH_001
```

### 3.4 已实现学习引擎

服务层：

- `TeachingManager`
- `LearningEngine`
- `RuleApplicationEngine`

规则类型预留：

- `header_alias`
- `field_location`
- `appendix_table_hint`
- `terminology_alias`
- `row_identity_logic`
- `unit_conversion_hint`
- `value_extraction_pattern`

当前策略：

- 有 LLM 时，`teach learn` 调用 LLM 归纳规则。
- 无 LLM 或 LLM 解析失败时，用本地 grouping 生成规则。
- 规则写入 SQLite。
- 同步写入：
  - `memory/extraction_rules.yaml`
  - `memory/extraction_memory.md`
- 低置信或非 low-risk 规则进入 review。

UI 启示：

- 需要“示教面板”：
  - 用户选择缺失字段。
  - 输入/粘贴证据位置。
  - 填值、单位、样品 ID。
  - 保存后立即看到 patch。
- 需要“规则学习页”：
  - 展示由示教归纳出的规则。
  - 显示适用字段、证据、置信度、风险。
  - 支持确认、拒绝、编辑、提升作用域。

---

## 4. WP6 Export, Trace & Cost

### 4.1 标准化导出

已具备能力：

- 导出 CSV/XLSX：

```bash
geochem export \
  --project ARTICLE_TEST_001 \
  --table TBL_005 \
  --headers-json tests/奥陶纪地化数据_headers.json \
  --format csv
```

- 导出候选数据：

```bash
geochem export --project ARTICLE_TEST_001 --table TBL_005 --candidates
```

- 正式导出按用户表头输出完整列。
- 缺数据列保留为空。
- 追加治理字段：
  - `Source_File`
  - `Source_Table`
  - `Source_Row`
  - `Original_Field`
  - `Original_Unit`
  - `Original_Value`
  - `Mapping_Rule_ID`
  - `Calculation_ID`
  - `Review_Status`
  - `Confidence`
  - `Processed_At`
  - `Quality_Grade`

### 4.2 审计包导出

新增能力：

```bash
geochem export \
  --project ARTICLE_TEST_001 \
  --table TBL_005 \
  --headers-json tests/奥陶纪地化数据_headers.json \
  --format xlsx \
  --package
```

审计包目录包含：

- 标准化数据文件
- `candidate_tables.csv`
- `review_items.csv`
- `review_decisions.csv`
- `mapping_rules.csv`
- `learned_extraction_rules.csv`
- `record_patches.csv`
- `teaching_events.csv`
- `trace_rows.csv`
- `trace_summary.md`
- `llm_cost_report.csv`
- `llm_cost_report.md`
- `calculations/`

每次导出写入 `export_jobs`：

- project
- article
- table
- format
- output path
- package path
- record count
- status
- created at

UI 启示：

- 需要“导出中心”：
  - 数据导出。
  - 审计包导出。
  - 历史 export jobs。
  - 打开导出目录。
  - 查看导出状态和记录数。

### 4.3 行级追溯

已具备能力：

行级追溯：

```bash
geochem trace row --project ARTICLE_TEST_001 --record STD_001
```

表级追溯汇总：

```bash
geochem trace table --project ARTICLE_TEST_001 --table TBL_005
```

trace row 包含：

- record ID
- article ID
- table ID
- row ID
- source file
- source table
- source row
- quality grade
- standardized data
- original fields
- review statuses
- confidence scores
- patches
- reviews
- calculations

当前追溯粒度：

- 已实现行级。
- 未做单元格级深追溯 UI。
- 但标准化记录内部已经保留 per-field JSON，可作为后续单元格级追溯基础。

UI 启示：

- 标准化结果表每一行应有“Trace”按钮。
- Trace 页面建议分 Tab：
  - 来源
  - 字段映射
  - 补值 patch
  - 审核记录
  - 计算记录
  - LLM 相关调用

### 4.4 成本报告

已具备能力：

```bash
geochem llm cost --project ARTICLE_TEST_001
geochem llm cost --project ARTICLE_TEST_001 --group-by task
geochem llm cost --project ARTICLE_TEST_001 --group-by article
geochem llm cost --project ARTICLE_TEST_001 --group-by provider
```

审计包中自动生成：

- `llm_cost_report.csv`
- `llm_cost_report.md`

UI 启示：

- 成本页应支持：
  - 总调用数
  - 总 tokens
  - 总 estimated cost
  - 按模型/任务/文章/provider 切换
  - 异常调用筛选

---

## 5. 当前数据库能力地图

| 数据表 | 当前用途 | UI 对应页面/组件 |
|---|---|---|
| `projects` | 项目信息 | 项目列表、项目设置 |
| `articles` | 论文元数据 | 文章列表、文章详情 |
| `resources` | PDF/Excel/CSV/HTML 等资源 | 资源列表 |
| `sections` | 论文章节结构 | 文档解析视图 |
| `table_assets` | 原始表资产 | 表格资源列表 |
| `candidate_tables` | 候选表 | 候选表浏览器 |
| `candidate_columns` | 候选列、单位、样例值 | 字段映射工作台 |
| `candidate_rows` | 原始候选行 | 候选数据预览 |
| `field_mappings` | 字段映射建议 | 映射工作台 |
| `mapping_rules` | 已确认映射规则 | 规则库 |
| `review_items` | 待审核项 | 审核队列 |
| `review_decisions` | 审核决策 | 审核历史 |
| `calculation_records` | 换算记录 | 计算详情 |
| `standardized_records` | 标准化数据 | 标准化结果表 |
| `export_jobs` | 导出记录 | 导出中心 |
| `teaching_events` | 用户示教证据 | 示教面板、学习历史 |
| `learned_extraction_rules` | 学习规则 | 规则学习页 |
| `record_patches` | 补值记录 | Patch 列表、Trace |
| `llm_calls` | LLM 调用与成本 | 成本页、调用日志 |
| `processing_events` | 处理事件 | 任务日志 |

---

## 6. 推荐 UI 信息架构

### 6.1 一级导航

建议 UI 左侧导航为：

1. Project Dashboard
2. Articles
3. Resources
4. Candidate Tables
5. Mapping
6. Review
7. Teaching / Agent Learning
8. Standardized Data
9. Trace
10. Export
11. Cost
12. Settings

### 6.2 页面设计建议

#### Project Dashboard

核心信息：

- 项目 ID、名称、schema。
- 文章数、资源数、候选表数、标准化记录数。
- 待审核项。
- learned rules 数。
- 最近导出。
- LLM 成本总览。

主要操作：

- 导入论文/文件。
- 导入 schema。
- 开始抽取。
- 查看待审核。
- 导出审计包。

#### Articles

核心信息：

- DOI
- title
- year
- status
- resource count
- standardized count
- review count

主要操作：

- 导入 DOI。
- 绑定本地文件。
- 打开文章详情。

#### Resources

核心信息：

- resource type
- file name
- status
- local path/source url
- hash/size

主要操作：

- extract
- preview
- reimport

#### Candidate Tables

核心信息：

- table ID
- sheet/page
- row count
- col count
- extract method
- confidence

主要操作：

- 预览表格。
- 查看列。
- map。
- export candidates。

#### Mapping Workbench

核心信息：

- 字段分组。
- 原始字段。
- 目标字段。
- source unit/target unit。
- mapping type。
- confidence。
- risk。
- requires review。
- reason。

主要操作：

- 自动 map。
- grouped LLM fallback。
- 编辑映射。
- 发送 review。
- 保存 rule。

#### Review Queue

核心信息：

- review ID。
- item type。
- risk。
- original field/unit/value。
- AI suggestion。
- confidence。
- evidence。

主要操作：

- accept。
- reject。
- edit。
- defer。
- save rule。

#### Teaching / Agent Learning

核心信息：

- 用户示教事件列表。
- patch 列表。
- learned rule 列表。
- 规则状态：pending/confirmed/rejected/deferred。

主要操作：

- 添加示教。
- 从示教学习规则。
- 应用规则。
- 查看 patch explain。
- 发送规则审核。

#### Standardized Data

核心信息：

- 用户完整表头。
- 数据值。
- 缺失值。
- quality grade。
- source row。
- review status。

主要操作：

- filter by grade。
- filter missing fields。
- open trace。
- export。

#### Trace

核心信息：

- record row。
- source file/table/row。
- patches。
- reviews。
- calculations。
- confidence。
- quality grade。

主要操作：

- 查看行级 trace。
- 打开证据。
- 打开计算档案。

#### Export Center

核心信息：

- export jobs。
- output path。
- package path。
- record count。
- status。
- created at。

主要操作：

- export CSV/XLSX。
- export audit package。
- open output folder。

#### Cost

核心信息：

- model/provider。
- task/agent/article。
- call count。
- input/output/total tokens。
- estimated cost。

主要操作：

- group by。
- export report。
- open LLM call detail。

---

## 7. 推荐端到端 CLI 工作流

### 7.1 常规单篇论文补充 Excel 流程

```bash
geochem import-file \
  --project ARTICLE_TEST_001 \
  --file tests/奥陶纪地化数据.xlsx \
  --type supplementary_excel

geochem extract \
  --project ARTICLE_TEST_001 \
  --resource RES_001

geochem map \
  --project ARTICLE_TEST_001 \
  --table TBL_005 \
  --grouped \
  --header-descriptions tests/奥陶纪地化数据_headers.json \
  --provider xiaomi \
  --model mimo-v2.5-pro

geochem review list --project ARTICLE_TEST_001

geochem standardize \
  --project ARTICLE_TEST_001 \
  --table TBL_005

geochem export \
  --project ARTICLE_TEST_001 \
  --table TBL_005 \
  --headers-json tests/奥陶纪地化数据_headers.json \
  --format csv
```

### 7.2 用户发现漏值后的进化流程

```bash
geochem teach add \
  --project ARTICLE_TEST_001 \
  --article ARTICLE_TEST_001 \
  --table TBL_005 \
  --sample-id XM-01 \
  --target-header "Li ppm" \
  --target-field Li \
  --unit ppm \
  --value 42.1 \
  --source appendix \
  --evidence "Supplementary Table S1, column Li(ppm), row sample XM-01"

geochem teach learn \
  --project ARTICLE_TEST_001 \
  --article ARTICLE_TEST_001 \
  --provider xiaomi \
  --model mimo-v2.5-pro

geochem teach apply \
  --project ARTICLE_TEST_001 \
  --table TBL_005

geochem review list --project ARTICLE_TEST_001

geochem standardize \
  --project ARTICLE_TEST_001 \
  --table TBL_005
```

### 7.3 审计交付流程

```bash
geochem trace table \
  --project ARTICLE_TEST_001 \
  --table TBL_005

geochem llm cost \
  --project ARTICLE_TEST_001 \
  --group-by task

geochem export \
  --project ARTICLE_TEST_001 \
  --table TBL_005 \
  --headers-json tests/奥陶纪地化数据_headers.json \
  --format xlsx \
  --package
```

---

## 8. 当前验收结果与测试覆盖

当前单测覆盖：

- 数据库表初始化。
- HeaderNormalizer 特殊表头。
- TargetHeaderBuilder 保留 display header 和单位。
- headers.json 容错加载。
- 字段分组。
- MappingEngine deterministic mapping。
- MappingEngine memory 优先。
- ReviewManager 审核与规则保存。
- UnitConversionEngine 换算。
- StandardizationPipeline 审核策略。
- TeachingManager 示教与 patch。
- LearningEngine 规则学习与 memory 同步。
- RuleApplicationEngine 规则应用。
- 低置信 learned rule 进入 review。
- TraceService 行级追溯。
- CostReporter 成本聚合。
- AuditPackageBuilder 审计包与 export job。

当前测试结果：

```text
169 passed, 5 warnings
```

---

## 9. 当前边界与后续开发重点

### 9.1 当前边界

当前阶段不包含：

- PySide6/UI 页面实现。
- 图像数值提取。
- 自动绕过验证码或机构登录。
- 多论文无人值守批处理。
- 单元格级 UI trace。
- 复杂数据库迁移系统。
- 强制预算拦截。

### 9.2 后续 UI 前应补强的工程点

建议 UI 开始前优先补强：

1. 增加稳定的服务层 API，减少 UI 直接调用 CLI 逻辑。
2. 给 teach/review/trace/export 增加结构化 JSON 输出模式。
3. 增加任务状态表或 processing events 统一事件流。
4. 增加项目内 table/article 的查询 API。
5. 增加 export package zip 选项。
6. 增加对 learned rule 的编辑命令。
7. 增加字段缺失率和质量评分统计。
8. 增加集成测试覆盖完整真实 Excel 工作流。

### 9.3 后续 UI 的最小可用版本建议

UI MVP 不必一次性实现全部页面，推荐顺序：

1. Project Dashboard
2. Candidate Table Preview
3. Mapping Workbench
4. Review Queue
5. Standardized Data
6. Teaching Panel
7. Trace Drawer
8. Export Center
9. Cost Report

这样能优先覆盖用户最核心的操作链：

```text
看候选表
→ 看映射
→ 审核
→ 发现漏值
→ 示教
→ 标准化
→ trace
→ 导出审计包
```

---

## 10. 阶段性交付摘要

本阶段交付的系统已经具备科研数据整理软件的核心骨架：

- 能接收论文和补充材料。
- 能完整保存候选表。
- 能按照用户表头和单位输出。
- 能做规则优先映射和 LLM 补缺。
- 能人工审核高风险项。
- 能记忆用户确认过的规则。
- 能进行单位和化学形态换算。
- 能通过用户示教补全漏值/漏行。
- 能从示教中学习抽取规则。
- 能生成标准化记录。
- 能输出行级 trace。
- 能统计 LLM 成本。
- 能导出数据和审计包。

对于后续 UI 来说，当前 CLI 和数据库已经定义了清晰的信息对象：

- project
- article
- resource
- candidate table
- target header
- field mapping
- review item
- teaching event
- learned rule
- record patch
- standardized record
- trace row
- export job
- LLM call

这些对象可以直接转化为 UI 页面、列表、详情抽屉、审核弹窗和导出中心。下一阶段的重点不再是“有没有核心能力”，而是把这些能力组织成研究人员能顺畅使用的交互工作台。
