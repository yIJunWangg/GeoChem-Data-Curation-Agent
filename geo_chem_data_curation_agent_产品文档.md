# GeoChem Data Curation Agent 产品文档

## 1. 产品名称

**GeoChem Data Curation Agent**  
中文名称：**地球化学文献数据整理与标准化 Agent**

---

## 2. 产品定位

GeoChem Data Curation Agent 是一款面向地质学、沉积学、地球化学、古气候与资源环境研究的数据采集与标准化软件。产品目标不是简单下载论文或做普通文献总结，而是围绕用户预先定义的数据库表头，对论文正文、补充材料、表格与后续版本中的图像数据进行自动识别、抽取、标准化、审核和入库。

该系统采用 **Schema-driven Agent** 设计思想，即用户先固定数据库字段与数据标准，AI Agent 在此基础上读取论文及其附属资料，判断哪些内容与数据库字段相关，并将原始数据转换为可入库的标准化数据。在字段名称、单位、化学形态、计算规则或数据来源存在不确定性时，系统不会静默决策，而是进入人工审核流程。用户确认后的规则会被写入长期映射记忆，后续遇到相似情况时自动复用。

产品核心目标是构建一个 **可追溯、可审核、可自我积累规则的地球化学文献数据整理工作流**。

---

## 3. 产品背景与痛点

地质学论文中的地球化学数据通常分散在正文表格、补充材料、附录文件、数据仓库链接以及图像中。不同论文之间的数据组织方式、字段命名、单位表达和化学形态存在显著差异。例如，同一类数据可能被写作 Na、Na₂O、Na2O、Sodium、Na wt%、Na2O wt%、ppm Na 等不同形式。对于数据库建设而言，单纯提取数据并不够，还必须知道这些字段是否对应数据库表头、是否需要换算、换算过程是否正确、数据来源是否可以溯源。

当前人工整理地球化学数据存在以下问题：

1. **数据来源分散**：正文、补充材料、网页附件、Excel、CSV、PDF 表格和图片可能同时包含相关数据。
2. **字段命名不统一**：不同文献对同一元素、氧化物或指标的命名方式不同。
3. **单位与化学形态复杂**：wt%、ppm、mg/kg、μg/g、oxide、element 等需要区分。
4. **人工判断成本高**：研究人员需要逐篇阅读、下载、识别、复制和校对。
5. **数据可信度难追溯**：后续很难快速确认某个入库值来自哪篇论文、哪个附件、哪个表格、哪个字段以及经过什么换算。
6. **AI 自动化存在幻觉风险**：如果没有审核和证据链机制，AI 可能错误映射字段或误用单位。
7. **重复规则无法沉淀**：用户每次都需要重新判断类似 Na2O→Na、K2O→K、SiO₂→SiO2 等字段关系。

GeoChem Data Curation Agent 的价值在于：在自动化提高效率的同时，通过人工审核、规则记忆、计算归档和来源追踪确保数据可靠。

---

## 4. 目标用户

### 4.1 主要用户

1. 地质学、地球化学、沉积学、古气候方向研究生与科研人员；
2. 需要批量整理文献地化数据的课题组；
3. 建设地球化学数据库、沉积物数据库或区域地质数据集的团队；
4. 需要从论文及补充材料中提取表格数据的科研助理；
5. 地学数据产品、知识库或文献数据平台开发者。

### 4.2 使用场景

1. 用户正在建设一个现代河流砂组分数据库；
2. 用户需要从古气候、沉积学或地球化学论文中批量收集元素含量；
3. 用户需要整理论文补充材料中的 Excel/CSV 表格；
4. 用户希望将不同论文中的字段统一到固定数据库表头；
5. 用户需要为每一个入库数据保留来源、换算过程和审核记录；
6. 用户希望通过 AI Agent 逐步积累字段映射和单位换算规则。

---

## 5. 产品目标

### 5.1 总体目标

实现一个面向地球化学文献数据整理的半自动化 Agent 系统，使用户输入一篇论文或论文网页后，系统能够自动发现正文、补充材料和相关数据文件，识别与用户表头相关的数据内容，抽取候选数据，完成字段映射、单位识别、计算换算、人工审核、规则记忆和标准化导出。

### 5.2 V1 阶段目标

V1 阶段优先完成 **表格型数据整理**，暂不强求图像数值提取。

V1 目标包括：

1. 支持用户上传或定义固定数据库表头；
2. 支持导入论文 PDF、网页链接、DOI、本地补充材料；
3. 自动生成论文资源清单；
4. 自动区分正文、章节、表格、图像、补充材料；
5. 自动判断表格和附件是否与目标表头相关；
6. 从 PDF、HTML、Excel、CSV、TXT 中抽取候选数据；
7. 自动提出字段映射与单位换算建议；
8. 对不确定映射、换算和字段含义进入人工审核；
9. 将用户确认的映射关系写入长期规则记忆；
10. 对所有换算过程建立可追溯计算档案；
11. 输出标准化 Excel/CSV/SQLite 数据；
12. 输出处理日志、证据链、token 消耗统计和成本面板。

### 5.3 V2 阶段目标

V2 阶段加入图像相关能力：

1. 自动判断论文图片是否与目标地化数据相关；
2. 区分地质图、剖面图、散点图、Harker 图、REE 配分图、蛛网图、柱状图、三角图等；
3. 对相关图片建立待处理清单；
4. 支持半自动图像数值提取；
5. 将图像提取结果纳入人工审核和来源追踪。

### 5.4 V3 阶段目标

V3 阶段实现批量化与数据库级管理：

1. 支持多篇论文批量处理；
2. 支持数据版本管理；
3. 支持团队协作审核；
4. 支持数据库直连入库；
5. 支持多项目、多表头、多规则集管理；
6. 支持统计仪表盘与质量控制报告。

---

## 6. 核心设计原则

### 6.1 表头驱动

所有数据抽取、字段判断和单位转换都必须围绕用户预先定义的数据库表头进行。AI 不应脱离用户表头自行扩展大量无关字段。

### 6.2 人机协同

系统不能假设 AI 的判断永远正确。凡是涉及字段歧义、单位不明、氧化物与元素换算、公式推导、数据来源不清或样品合并关系复杂的情况，都应进入人工审核。

### 6.3 可追溯

每一个进入数据库的值都应能够追溯到：

1. 原始论文；
2. DOI 或 URL；
3. 原始文件；
4. 原始表格或 sheet；
5. 原始字段名；
6. 原始单位；
7. 原始数值；
8. 映射规则；
9. 换算公式；
10. 计算过程；
11. 用户审核状态；
12. 入库时间和操作记录。

### 6.4 可学习

系统不是一次性工具，而应随着用户审核不断积累规则。用户确认过的字段映射、单位换算、别名规范、跳过规则和特殊处理规则应写入长期记忆，后续自动复用。

### 6.5 可审计

系统每一次调用 AI、每一次字段映射、每一次单位换算、每一次人工确认、每一次导出都应生成日志。日志应包括 token 消耗、模型名称、输入摘要、输出摘要、时间戳、任务 ID 和处理结果。

---

## 7. 总体工作流

完整处理流程如下：

```text
用户创建项目
→ 上传或定义数据库表头
→ 输入论文 URL / DOI / PDF / 补充材料
→ 系统建立文章任务
→ 资源发现：PDF、HTML、补充材料、附件、数据链接
→ 文档解析：章节、表格、图片、参考文献、数据可用性说明
→ 相关性判断：哪些资源与目标字段相关
→ 表格抽取：PDF/HTML/Excel/CSV/TXT
→ 候选数据生成
→ 字段映射与单位识别
→ 查询映射记忆库
→ 自动处理高置信度规则
→ 不确定项进入人工审核
→ 用户确认映射与换算方式
→ 写入 mapping memory
→ 执行单位换算和化学形态换算
→ 保存计算过程档案
→ 生成标准化数据表
→ 生成证据链与处理日志
→ 统计 token 消耗和任务成本
→ 用户查看、修正、导出或入库
```

---

## 8. 功能模块设计

## 8.1 项目管理模块

### 功能说明

用于管理不同数据整理任务。每个项目可对应一个数据库表头、一套字段映射规则、一批论文和一套导出标准。

### 主要功能

1. 新建项目；
2. 导入已有项目；
3. 设置项目名称、研究方向、数据库类型；
4. 绑定数据库表头；
5. 绑定映射规则库；
6. 查看项目处理进度；
7. 查看项目 token 消耗；
8. 导出项目完整归档。

### 项目元数据示例

```yaml
project_name: Global River Sediment Geochemistry
project_id: GRC_2026_001
created_at: 2026-xx-xx
schema_file: schema/geochem_schema.yaml
mapping_memory: memory/mapping_rules.yaml
output_database: output/standardized_data.sqlite
```

---

## 8.2 表头配置模块

### 功能说明

用户先固定数据库表头，并为每个字段定义基本含义、可接受别名、默认单位、数据类型和审核策略。

### 表头配置内容

每个字段建议包含：

1. 字段名；
2. 字段描述；
3. 数据类型；
4. 默认单位；
5. 可接受别名；
6. 是否允许换算；
7. 是否必须人工审核；
8. 缺失值处理方式；
9. 是否为主键或合并键；
10. 入库质量等级要求。

### 示例

```yaml
columns:
  - name: Sample_ID
    type: string
    description: Sample identifier
    required: true
    aliases: ["Sample", "Sample ID", "Sample No.", "样品编号"]

  - name: Na
    type: float
    description: Elemental sodium concentration
    default_unit: wt%
    aliases: ["Na", "Sodium"]
    allow_conversion: true
    review_required_for: ["Na2O", "Na₂O", "ppm_to_wt%"]

  - name: Na2O
    type: float
    description: Sodium oxide concentration
    default_unit: wt%
    aliases: ["Na2O", "Na₂O", "sodium oxide"]
    allow_conversion: true

  - name: CIA
    type: float
    description: Chemical Index of Alteration
    default_unit: dimensionless
    aliases: ["CIA", "Chemical Index of Alteration"]
```

---

## 8.3 论文输入与访问模块

### 功能说明

支持用户输入论文来源，并建立文章级任务。

### 支持输入类型

1. DOI；
2. 论文网页 URL；
3. 本地 PDF；
4. 本地补充材料；
5. ZIP 文件；
6. Excel/CSV 数据文件；
7. 用户手动添加的数据仓库链接。

### 机构登录与人机验证策略

系统不应自动绕过验证码或机构权限。合理策略是：

1. 内置浏览器或调用系统浏览器；
2. 用户自行登录机构账号；
3. 用户自行完成人机验证；
4. 系统在用户授权后的页面中读取可访问资源；
5. 不保存用户机构账号和密码；
6. 对下载资源建立本地缓存和来源记录。

---

## 8.4 文献资源发现模块

### 功能说明

自动发现论文相关资源，包括 PDF、HTML 正文、补充材料、数据可用性链接、附件和可下载文件。

### 识别对象

1. PDF 正文；
2. HTML 正文；
3. Supplementary information；
4. Supporting information；
5. Appendix；
6. Table S1、Table S2 等；
7. Figure S1、Figure S2 等；
8. Excel、CSV、TXT、DOCX、ZIP、PDF 附件；
9. Data availability 中的数据仓库链接；
10. GitHub、Zenodo、Figshare、Dryad 等数据入口。

### 输出资源清单

```csv
resource_id,resource_type,file_name,source_url,local_path,relevance_status,next_action
R001,main_pdf,article.pdf,https://...,raw/articles/article.pdf,pending,parse
R002,supplementary_excel,Table_S1.xlsx,https://...,raw/supplementary/Table_S1.xlsx,pending,parse
R003,figure,Fig_5.png,article_pdf_page_8,raw/figures/Fig_5.png,pending,v2_review
```

---

## 8.5 文档解析 Skill 模块

### 功能说明

系统内置一组文档解析 skill，用于区分论文章节、表格、图片、图注、表注、参考文献和补充材料结构。

### 主要 Skill

#### 8.5.1 Section Parser Skill

识别论文结构：

1. Title；
2. Abstract；
3. Introduction；
4. Geological setting；
5. Materials and methods；
6. Results；
7. Discussion；
8. Conclusion；
9. Data availability；
10. References；
11. Supplementary information。

#### 8.5.2 Table Parser Skill

识别正文表格、PDF 表格、HTML 表格和补充材料表格。

输出内容包括：

1. 表格编号；
2. 表题；
3. 表注；
4. 原始列名；
5. 原始单位；
6. 行数列数；
7. 是否包含样品编号；
8. 是否包含目标字段；
9. 推荐处理方式。

#### 8.5.3 Figure Parser Skill

V1 阶段只做图像分类和相关性判断；V2 阶段增加数值提取。

识别内容包括：

1. Figure 编号；
2. 图题与图注；
3. 图像类型；
4. 是否包含地球化学数据；
5. 是否需要 V2 图像数值提取；
6. 是否仅为背景地质图或剖面图。

#### 8.5.4 Supplement Parser Skill

识别补充材料结构，包括 Excel 多 sheet、PDF 附录表格、CSV 数据文件和 ZIP 内部文件。

---

## 8.6 相关性判断模块

### 功能说明

判断论文资源是否与用户表头相关，避免对无关内容进行深度处理。

### 判断对象

1. 整篇论文是否相关；
2. 某个章节是否包含数据说明；
3. 某个表格是否包含目标字段；
4. 某个补充材料是否是主数据表；
5. 某个图像是否可能包含目标地化数据。

### 输出示例

```csv
item_id,item_type,title,relevance_score,reason,next_action
T001,table,"Major element compositions",0.95,"Contains SiO2, Al2O3, Na2O, K2O and sample IDs",extract
T002,table,"Experimental conditions",0.20,"No geochemical concentration data",skip
F003,figure,"Harker diagrams",0.80,"Shows major oxide variation but image extraction is V2 task",mark_for_v2
S001,supplement,"Table S1.xlsx",0.98,"Contains raw sample geochemistry",extract
```

---

## 8.7 表格抽取模块

### 功能说明

从不同来源抽取候选数据表。

### 支持格式

1. PDF 表格；
2. HTML 表格；
3. Excel；
4. CSV；
5. TXT；
6. DOCX 表格；
7. ZIP 内部表格文件。

### 抽取结果要求

每一个抽取表必须保留：

1. 原始文件路径；
2. 原始页码或 sheet 名；
3. 表格编号；
4. 原始表题；
5. 原始列名；
6. 原始单位；
7. 原始数据；
8. 抽取方法；
9. 抽取置信度。

---

## 8.8 字段映射模块

### 功能说明

将论文中的原始字段映射到用户数据库表头。

### 映射类型

1. 完全一致映射：SiO2 → SiO2；
2. 符号规范映射：SiO₂ → SiO2；
3. 别名映射：Chemical Index of Alteration → CIA；
4. 单位换算映射：Na ppm → Na wt%；
5. 化学形态换算：Na2O → Na；
6. 需要新建字段：FeOT 未在当前 schema 中定义；
7. 不确定映射：TFe、FeO*、Fe2O3T 等。

### 置信度策略

| 置信度 | 动作 |
|---|---|
| ≥ 0.90 | 可自动映射，但仍记录日志 |
| 0.70–0.90 | 进入审核队列，给出建议 |
| < 0.70 | 必须人工确认 |
| 涉及氧化物/元素换算 | 默认至少人工确认一次 |
| 涉及单位不明 | 必须人工确认 |
| 涉及字段多义性 | 必须人工确认 |

---

## 8.9 单位换算与化学形态换算模块

### 功能说明

负责处理单位标准化和化学形态转换。所有换算必须保留计算过程。

### 支持换算类型

1. ppm ↔ wt%；
2. mg/kg ↔ ppm；
3. μg/g ↔ ppm；
4. oxide ↔ element；
5. 百分比 ↔ 小数；
6. 用户自定义公式；
7. 指标计算，如 CIA、WIP 等。

### 示例：Na2O 转 Na

若目标字段为元素 Na，而原始字段为 Na2O wt%，则公式为：

```text
Na = Na2O × (2 × atomic_weight_Na) / molecular_weight_Na2O
```

近似为：

```text
Na = Na2O × 0.7419
```

该换算必须保存到计算归档中，不能只保存最终结果。

---

## 8.10 计算过程归档模块

### 功能说明

所有进入数据库的数据，如果经历了单位换算、字段转换、公式计算或人工修正，都必须保留完整计算过程。用户后续查看任意一个入库值时，可以追溯到计算细节。

### 归档内容

每条计算记录应包括：

1. 计算 ID；
2. 数据行 ID；
3. 目标字段；
4. 原始字段；
5. 原始值；
6. 原始单位；
7. 目标单位；
8. 使用公式；
9. 公式来源；
10. 代入过程；
11. 计算结果；
12. 有效数字处理方式；
13. 是否经过人工确认；
14. 操作时间；
15. 操作人；
16. 关联论文和表格来源。

### 文件夹结构

```text
project_root/
  calculations/
    article_001/
      calc_summary.csv
      calc_Na2O_to_Na.md
      calc_K2O_to_K.md
      calc_CIA.md
      row_level_calculations.jsonl
```

### 单条计算记录 Markdown 示例

```markdown
# Calculation Record: CALC_000124

## Target Field
Na

## Source Information
- Article: Zhang et al., 2020
- DOI: 10.xxxx/xxxxx
- Source file: Table_S1.xlsx
- Sheet: Major elements
- Original field: Na2O
- Original unit: wt%
- Original value: 2.87

## Mapping Decision
- Mapping type: oxide_to_element
- Decision: Convert Na2O wt% to elemental Na wt%
- Review status: confirmed by user
- Mapping rule ID: MAP_NA2O_TO_NA_001

## Formula
Na = Na2O × (2 × atomic_weight_Na) / molecular_weight_Na2O

## Constants
- atomic_weight_Na = 22.98976928
- molecular_weight_Na2O = 61.97894
- conversion_factor = 0.741857

## Calculation
Na = 2.87 × 0.741857 = 2.12913 wt%

## Stored Value
2.129

## Notes
Rounded to three decimal places according to project output settings.
```

---

## 8.11 人工审核模块

### 功能说明

将所有不确定项整理成人工审核任务，由用户确认后再执行映射、换算或入库。

### 审核类型

1. 字段映射审核；
2. 单位换算审核；
3. 化学形态换算审核；
4. 样品 ID 合并审核；
5. 表格相关性审核；
6. 新字段创建审核；
7. 数据跳过审核；
8. 图像是否进入 V2 处理审核。

### 审核界面应展示

1. 原始字段；
2. 原始单位；
3. 原始表格片段；
4. AI 建议映射；
5. 置信度；
6. 建议理由；
7. 可能风险；
8. 可选操作。

### 用户可选操作

1. 接受 AI 建议；
2. 修改映射字段；
3. 选择换算公式；
4. 新建字段；
5. 跳过该字段；
6. 将规则保存为长期记忆；
7. 仅对当前论文生效；
8. 标记为以后再处理。

---

## 8.12 映射记忆模块

### 功能说明

记录用户确认过的字段映射、单位换算、特殊规则和跳过策略。

### 文件形式

建议同时保存两种形式：

1. `mapping_rules.yaml`：供系统机器读取；
2. `mapping_memory.md`：供用户查看、编辑和审计。

### YAML 示例

```yaml
rules:
  - rule_id: MAP_NA2O_TO_NA_001
    source_field: Na2O
    target_field: Na
    source_unit: wt%
    target_unit: wt%
    mapping_type: oxide_to_element
    formula: "Na = Na2O * 0.741857"
    review_status: confirmed
    apply_scope: project
    created_at: 2026-xx-xx
    created_by: user

  - rule_id: MAP_SIO2_SYMBOL_001
    source_field: SiO₂
    target_field: SiO2
    mapping_type: alias_normalization
    formula: null
    review_status: confirmed
    apply_scope: global
```

### Markdown 示例

```markdown
# Mapping Memory

## Confirmed Rules

### MAP_NA2O_TO_NA_001
- Source field: Na2O
- Target field: Na
- Source unit: wt%
- Target unit: wt%
- Mapping type: oxide to element
- Formula: Na = Na2O × 0.741857
- Review status: confirmed
- Scope: current project

## Pending Rules

### FeOT
- Possible meaning: total iron as FeO
- Action: ask user before importing
```

---

## 8.13 标准化导出模块

### 功能说明

将审核后的数据导出为可进入数据库的标准化格式。

### 输出格式

1. Excel；
2. CSV；
3. SQLite；
4. JSONL；
5. Parquet；
6. SQL insert 脚本。

### 标准输出字段建议

除用户表头外，还应追加数据治理字段：

```text
Reference
DOI
Source_File
Source_Table
Source_Row
Original_Field
Original_Unit
Original_Value
Mapped_Field
Mapped_Unit
Stored_Value
Mapping_Rule_ID
Calculation_ID
Review_Status
Confidence
Processed_At
```

### 输出示例

```csv
Sample_ID,Na,SiO2,Reference,Source_File,Original_Field,Original_Unit,Mapping_Rule_ID,Calculation_ID,Review_Status
S1,2.129,65.2,Zhang et al. 2020,Table_S1.xlsx,Na2O,wt%,MAP_NA2O_TO_NA_001,CALC_000124,confirmed
```

---

## 8.14 溯源查看模块

### 功能说明

用户点击任意一个入库值，可以查看其完整来源和处理过程。

### 溯源信息包括

1. 论文题目；
2. 作者和年份；
3. DOI；
4. 原始文件；
5. 原始表格；
6. 原始字段；
7. 原始单位；
8. 原始值；
9. 映射规则；
10. 换算公式；
11. 计算过程；
12. 人工审核记录；
13. token 调用记录；
14. 日志文件位置。

### 用户操作

1. 查看原始表格；
2. 查看计算过程；
3. 查看 AI 判断理由；
4. 查看人工审核记录；
5. 回滚该条映射；
6. 修改规则并重新计算；
7. 导出单条溯源报告。

---

## 8.15 Token 消耗统计模块

### 功能说明

系统需要精细记录每一次 AI 调用的 token 消耗，并形成统计面板。

### 每次调用记录内容

1. 调用 ID；
2. 任务 ID；
3. 文章 ID；
4. Agent 名称；
5. Skill 名称；
6. 模型名称；
7. 输入 token；
8. 输出 token；
9. 总 token；
10. 估算费用；
11. 调用开始时间；
12. 调用结束时间；
13. 耗时；
14. 成功或失败；
15. 重试次数；
16. 输入摘要；
17. 输出摘要。

### token 日志示例

```json
{"call_id":"LLM_000231","article_id":"ART_001","agent":"SchemaMapper","skill":"field_mapping","model":"mimo-v2.5-pro","input_tokens":3210,"output_tokens":860,"total_tokens":4070,"estimated_cost":0.012,"status":"success","created_at":"2026-xx-xxT10:22:31"}
```

### 统计面板内容

#### 项目级统计

1. 总 token 消耗；
2. 输入 token；
3. 输出 token；
4. 总费用估算；
5. 按模型统计；
6. 按 Agent 统计；
7. 按文章统计；
8. 按任务类型统计；
9. 平均每篇论文消耗；
10. 失败调用次数；
11. 重试消耗。

#### 文章级统计

1. 当前论文总 token；
2. 资源发现消耗；
3. 章节解析消耗；
4. 相关性判断消耗；
5. 字段映射消耗；
6. 人工审核建议消耗；
7. 报告生成消耗。

#### Agent 级统计

| Agent | Calls | Input Tokens | Output Tokens | Total Tokens | Cost | Failure Rate |
|---|---:|---:|---:|---:|---:|---:|
| Resource Finder | 10 | 12000 | 3000 | 15000 | 0.05 | 0% |
| Schema Mapper | 35 | 72000 | 18000 | 90000 | 0.31 | 3% |
| Unit Normalizer | 20 | 28000 | 8000 | 36000 | 0.12 | 0% |

### 面板价值

1. 用户知道每篇论文处理成本；
2. 便于比较不同模型性价比；
3. 便于优化 Agent 流程；
4. 便于后续商业化计费；
5. 便于发现 token 消耗异常的任务。

---

## 9. Agent 架构设计

## 9.1 Agent 划分

### Agent 1：Resource Finder

负责发现论文正文、补充材料、附件和数据链接。

### Agent 2：Document Structure Parser

负责解析章节、表格、图片、图注、表注和数据可用性部分。

### Agent 3：Relevance Judge

负责判断资源是否与用户表头相关。

### Agent 4：Table Extractor

负责抽取候选表格数据。

### Agent 5：Schema Mapper

负责字段映射。

### Agent 6：Unit Normalizer

负责单位识别、单位换算和化学形态换算建议。

### Agent 7：Human Review Manager

负责整理不确定项，生成审核任务。

### Agent 8：Memory Writer

负责将用户确认写入映射记忆。

### Agent 9：Calculation Archivist

负责保存所有计算过程和换算档案。

### Agent 10：Database Exporter

负责标准化导出与数据库入库。

### Agent 11：Cost Monitor

负责 token 消耗统计、费用估算和面板展示。

---

## 9.2 Skill 体系

系统后续应内置多个可复用 skill：

1. `section_parse_skill`：论文章节识别；
2. `table_detect_skill`：表格检测；
3. `table_extract_skill`：表格抽取；
4. `figure_detect_skill`：图片检测；
5. `figure_relevance_skill`：图片相关性判断；
6. `supplement_discovery_skill`：补充材料发现；
7. `field_mapping_skill`：字段映射；
8. `unit_detect_skill`：单位识别；
9. `unit_conversion_skill`：单位换算；
10. `formula_archive_skill`：计算过程归档；
11. `review_task_skill`：人工审核任务生成；
12. `memory_update_skill`：映射记忆更新；
13. `export_skill`：标准化导出；
14. `token_accounting_skill`：token 统计。

---

## 10. 数据目录结构设计

建议每个项目使用如下目录结构：

```text
GeoChem_Project/
  project.yaml

  schema/
    geochem_schema.yaml
    output_schema.xlsx

  raw/
    articles/
      ART_001_main.pdf
    supplementary/
      ART_001_Table_S1.xlsx
      ART_001_supplement.pdf
    html/
      ART_001_page.html
    figures/
      ART_001_Fig_1.png
      ART_001_Fig_2.png

  parsed/
    ART_001/
      sections.json
      tables/
        table_001_raw.csv
        table_002_raw.csv
      figures/
        figure_inventory.csv
      resource_inventory.csv

  candidate_data/
    ART_001_candidates.xlsx
    ART_001_field_mapping_suggestions.xlsx

  review/
    ART_001_review_items.xlsx
    ART_001_review_decisions.jsonl

  memory/
    mapping_rules.yaml
    mapping_memory.md
    unit_conversion_rules.yaml

  calculations/
    ART_001/
      calc_summary.csv
      row_level_calculations.jsonl
      calc_Na2O_to_Na.md
      calc_CIA.md

  logs/
    ART_001_processing.log
    llm_calls.jsonl
    token_usage.csv
    errors.log

  output/
    standardized_data.xlsx
    standardized_data.csv
    standardized_data.sqlite
    extraction_report.md
    extraction_report.pdf

  reports/
    ART_001_traceability_report.md
    project_cost_report.xlsx
```

---

## 11. 用户界面设计

## 11.1 主界面

主界面包含：

1. 项目列表；
2. 当前项目进度；
3. 已处理论文数量；
4. 待审核任务数量；
5. token 消耗概览；
6. 最近错误提醒；
7. 快速导出入口。

## 11.2 表头配置页面

功能包括：

1. 上传 Excel 表头；
2. 编辑字段描述；
3. 设置默认单位；
4. 添加字段别名；
5. 设置是否允许换算；
6. 设置是否强制审核；
7. 保存为 schema 配置。

## 11.3 论文任务页面

功能包括：

1. 输入 DOI/URL；
2. 上传 PDF；
3. 上传补充材料；
4. 查看资源清单；
5. 查看章节解析结果；
6. 查看相关性判断；
7. 查看候选数据。

## 11.4 人工审核页面

功能包括：

1. 按严重程度显示待审核项；
2. 查看原始表格片段；
3. 查看 AI 建议；
4. 选择目标字段；
5. 选择换算规则；
6. 决定是否写入长期记忆；
7. 批量接受低风险映射；
8. 对高风险字段逐条确认。

## 11.5 溯源页面

功能包括：

1. 搜索样品；
2. 搜索字段；
3. 点击入库值查看来源；
4. 查看原始数据；
5. 查看计算过程；
6. 查看人工审核记录；
7. 查看 AI 调用记录；
8. 导出单条数据溯源报告。

## 11.6 Token 消耗面板

功能包括：

1. 项目总消耗；
2. 单篇论文消耗；
3. 单个 Agent 消耗；
4. 单个模型消耗；
5. 单次调用详情；
6. 费用估算；
7. 异常消耗提醒；
8. 导出成本报告。

---

## 12. 数据库设计建议

## 12.1 主要表

### articles

记录论文元数据。

```text
article_id
title
authors
year
doi
url
journal
status
created_at
```

### resources

记录论文相关资源。

```text
resource_id
article_id
resource_type
file_name
source_url
local_path
relevance_score
status
```

### raw_tables

记录原始表格。

```text
table_id
article_id
resource_id
table_title
page_or_sheet
raw_file_path
extract_method
confidence
```

### field_mappings

记录字段映射。

```text
mapping_id
source_field
target_field
source_unit
target_unit
mapping_type
rule_id
confidence
review_status
```

### calculations

记录计算过程。

```text
calculation_id
article_id
row_id
target_field
source_field
source_value
source_unit
target_value
target_unit
formula
rule_id
archive_file
review_status
```

### standardized_data

保存最终标准化数据。

字段由用户 schema 决定，同时追加来源字段。

### llm_calls

记录 AI 调用与 token 消耗。

```text
call_id
article_id
agent
skill
model
input_tokens
output_tokens
total_tokens
estimated_cost
status
created_at
```

---

## 13. 质量控制策略

### 13.1 数据质量等级

每条数据可标记为：

| 等级 | 含义 |
|---|---|
| A | 原始字段直接匹配，无需换算，来源清晰 |
| B | 字段别名映射，已确认 |
| C | 单位换算或化学形态换算，已确认并归档 |
| D | AI 建议但人工未确认，不建议入库 |
| E | 来源不清或字段歧义，禁止入库 |

### 13.2 入库规则

默认只有 A、B、C 级数据允许进入正式数据库。D 级数据可以进入候选库，E 级数据应被跳过。

### 13.3 错误回滚

如果用户发现某条规则错误，系统应支持：

1. 禁用该规则；
2. 查找所有使用该规则的数据；
3. 批量重新进入审核；
4. 重新计算相关字段；
5. 生成修正日志。

---

## 14. 权限、安全与合规

### 14.1 机构登录

系统不保存用户机构账号、密码或 Cookie，用户需自行完成人机验证和机构登录。系统只处理用户有权限访问并主动导入的文件。

### 14.2 数据版权

系统面向科研数据整理，应保留来源引用，不应将受版权保护的全文内容作为公开数据再分发。导出的数据库应重点保存数值数据、来源引用和必要证据链。

### 14.3 AI 调用隐私

用户可选择：

1. 使用云端大模型；
2. 使用本地模型；
3. 对敏感内容脱敏后再调用模型；
4. 禁止上传全文，仅上传表头和局部表格。

### 14.4 操作审计

所有关键操作必须有日志，尤其是：

1. 用户确认映射；
2. 用户修改公式；
3. 用户删除规则；
4. 用户导出正式数据；
5. 系统自动入库。

---

## 15. MVP 版本开发范围

### 15.1 MVP 必须实现

1. 项目创建；
2. 表头导入；
3. 本地 PDF 导入；
4. 本地补充材料导入；
5. Excel/CSV 表格读取；
6. PDF 表格初步抽取；
7. 字段映射建议；
8. 单位识别；
9. 人工审核；
10. 映射记忆；
11. 单位换算计算归档；
12. 标准化 Excel/CSV 导出；
13. 处理日志；
14. token 消耗统计。

### 15.2 MVP 暂不实现

1. 自动绕过验证码；
2. 完全自动机构登录；
3. 图像数值提取；
4. 多论文无人值守批量处理；
5. 团队协作；
6. 云端数据库部署；
7. 复杂图表数字化。

---

## 16. 推荐技术路线

### 16.1 桌面端优先

V1 建议采用本地桌面应用形式，便于处理本地 PDF、Excel 和机构网页下载后的文件。

推荐技术栈：

```text
Python
PySide6 / PyQt
pandas
openpyxl
pdfplumber
PyMuPDF
camelot / tabula
BeautifulSoup
Playwright
SQLite
YAML
Markdown
LLM API
```

### 16.2 后续 Web 化

当 V1 流程稳定后，可迁移为：

```text
FastAPI + React/Vue + SQLite/PostgreSQL + Worker Queue
```

### 16.3 Agent 编排

可采用轻量编排方式：

1. 每个 Agent 是一个 Python class；
2. 每个 Skill 是一个可独立测试的函数；
3. 所有 Agent 输入输出都保存为 JSON；
4. 每一步执行后写入日志；
5. 失败后允许从中间步骤恢复。

---

## 17. 验收标准

### 17.1 功能验收

1. 用户能够导入自定义表头；
2. 系统能够读取一篇论文及补充材料；
3. 系统能够生成资源清单；
4. 系统能够识别相关表格；
5. 系统能够提出字段映射建议；
6. 系统能够将不确定项交给用户审核；
7. 用户确认后系统能够保存规则；
8. 系统能够执行单位换算；
9. 系统能够保存计算过程；
10. 系统能够导出标准化数据；
11. 用户能够查看任意数据的来源；
12. 系统能够统计 token 消耗。

### 17.2 数据验收

1. 每个入库值必须有来源；
2. 每个换算值必须有计算记录；
3. 每个 AI 判断必须有日志；
4. 每个不确定字段必须有审核状态；
5. 正式导出数据不得包含未确认高风险字段。

### 17.3 性能验收

V1 阶段建议目标：

1. 单篇论文处理时间小于 5–10 分钟，不含人工审核时间；
2. 常见 Excel/CSV 补充材料读取准确率接近 100%；
3. PDF 表格抽取允许人工校正；
4. 字段映射建议对常见字段准确率达到较高水平；
5. token 消耗可完整统计。

---

## 18. 商业化潜力

该产品可作为以下方向的基础：

1. 科研数据整理工具；
2. 课题组内部数据平台；
3. 地球化学数据库建设工具；
4. 文献数据采集外包服务；
5. AI Agent 工作流产品；
6. 科研软件商业化原型；
7. 面向地质行业的数据治理工具。

潜在付费点包括：

1. 单机专业版；
2. 按论文处理数量计费；
3. 按 token 消耗计费；
4. 团队协作版；
5. 私有化部署；
6. 数据库建设项目服务；
7. 图像数值提取高级模块。

---

## 19. 风险与限制

### 19.1 技术风险

1. PDF 表格结构复杂，抽取不稳定；
2. 论文补充材料格式差异大；
3. AI 字段判断可能出现幻觉；
4. 单位和化学形态换算容易出错；
5. 图像数值提取难度较高。

### 19.2 数据风险

1. 原始论文数据本身可能有错误；
2. 样品编号可能跨表不一致；
3. 单位可能在表注而不是列名中；
4. 氧化物和元素字段可能混用；
5. 某些图像只展示趋势，不适合入库。

### 19.3 合规风险

1. 不能绕过机构登录和验证码；
2. 不能非法批量下载受限论文；
3. 不能公开传播受版权保护的全文或附件；
4. 应保留合理引用和来源信息。

---

## 20. 版本路线图

### V1：表格数据整理 Agent

重点实现：

1. 表头驱动；
2. 论文和补充材料导入；
3. 表格抽取；
4. 字段映射；
5. 单位换算；
6. 人工审核；
7. 计算归档；
8. token 面板；
9. 标准化导出。

### V2：图像识别与半自动提取

重点实现：

1. 图片类型识别；
2. 地化图像相关性判断；
3. Harker 图、REE 图、蛛网图识别；
4. 半自动图像数值提取；
5. 图像提取证据链。

### V3：批量数据库建设平台

重点实现：

1. 多论文批量处理；
2. 数据库直连；
3. 版本管理；
4. 团队审核；
5. 质量控制报告；
6. 私有化部署。

---

## 21. 产品一句话总结

GeoChem Data Curation Agent 是一个面向地球化学文献的数据整理 Agent，它以用户定义的数据库表头为核心，自动从论文正文、补充材料和后续图像中发现并抽取相关数据，通过字段映射、单位换算、人工审核、规则记忆、计算归档和 token 成本统计，最终生成可追溯、可审核、可入库的标准化地球化学数据。

