# GeoChem Data Curation Agent

Schema-driven AI agent for extracting, standardizing, and curating geochemical data from scientific literature.

## Overview

GeoChem Data Curation Agent automates the extraction of geochemical data (major elements, trace elements, isotopic ratios, etc.) from research papers and supplementary materials. It uses a schema-driven approach with multi-provider LLM support to map raw table data to standardized fields, with human review for high-risk mappings.

### V1 Minimum Closed Loop

> User imports a PDF paper and supplementary tables -> system extracts candidate data around user headers -> suggests field mappings and unit conversions -> flags uncertain items for human review -> saves confirmed rules and calculations -> exports traceable standardized Excel/CSV.

## Features

- **Schema-driven field mapping**: YAML-based geochem schema with aliases, Unicode normalization (SiO₂ -> SiO2), and chemical form classification
- **Multi-provider LLM system**: 16 providers supported (Anthropic, OpenAI, DeepSeek, Xiaomi MiMo, OpenRouter, Qwen, Zhipu, MiniMax, Moonshot, Doubao, Baichuan, Hunyuan, Yi, StepFun, Ollama)
- **Task-level model routing**: Different tasks (field mapping, relevance judging, unit suggestion) can use different models
- **Fallback chain**: Automatic provider fallback when primary model is unavailable
- **Risk-based review**: High-risk mappings (e.g., Na2O -> Na) require human confirmation
- **MemoryStore**: Confirmed mapping rules persisted for reuse across projects
- **Calculation archive**: Full provenance for unit conversions and formula calculations
- **Token tracking**: Every LLM call logged with token usage and latency
- **Positioned PDF evidence**: page-level tables, figures and paragraphs with normalized bounding boxes
- **Sample-level candidate grid**: exact user headers, conservative SampleID merge and cell evidence
- **Local Web UI**: React workbench backed by a localhost-only FastAPI service
- **CLI compatibility**: Existing curation pipeline remains operable from command line

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.10+ |
| Data models | Pydantic 2.x |
| Database | SQLite (WAL mode) |
| CLI | Typer + Rich |
| PDF parsing | PyMuPDF, pdfplumber |
| Excel/CSV | openpyxl, pandas |
| LLM SDKs | openai, anthropic |
| Config | PyYAML |
| Local API | FastAPI + SSE |
| UI | React + TypeScript + Vite |
| PDF viewer | PDF.js / React-PDF |
| Candidate grid | AG Grid Community |

## Installation

```bash
# Clone the repository
cd "GeoChem Data Curation Agent"

# Create virtual environment with Python 3.10+
python3.13 -m venv .venv
source .venv/bin/activate

# Install in editable mode
pip install -e ".[dev]"

# Install and build the Web UI
cd web
npm install
npm run build
cd ..
```

## Quick Start

### 1. Set up API keys

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
export DEEPSEEK_API_KEY="sk-..."
export MIMO_API_KEY="your-mimo-key"
export OPENROUTER_API_KEY="sk-or-..."
# ... other providers as needed
```

### Start the Web application

```bash
.venv/bin/geochem-ui
# Open http://127.0.0.1:8765
```

The React + FastAPI application is the sole graphical interface. The explicit
`.venv/bin/geochem-web` command starts the same Web service; `geochem-ui` is
kept as a backwards-compatible alias.

### 2. Create a project

```bash
geochem new-project "My Geochem Study" --desc "Basalt major elements"
```

### 3. Import a schema

```bash
geochem import-schema --project MY_GEOCHEM_STUDY --file config/schema_minimal.yaml
```

### 4. Check LLM providers

```bash
geochem llm list          # List all configured providers and models
geochem llm check         # Test connectivity of all providers
geochem llm test --provider xiaomi --model mimo-v2.5-pro -p "Hello"
```

### 5. View project status

```bash
geochem status --project MY_GEOCHEM_STUDY
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `geochem new-project <name>` | Create a new project |
| `geochem list-projects` | List all projects |
| `geochem status --project <id>` | Show project status |
| `geochem import-schema --project <id> --file <path>` | Import schema YAML |
| `geochem llm list` | List all LLM providers and models |
| `geochem llm check` | Test provider connectivity |
| `geochem llm test [task] --provider <name> -p <prompt>` | Test a provider |
| `geochem llm cost --project <id>` | Show LLM cost report |

## LLM Provider Configuration

All providers are configured in `config/settings.yaml`. API keys support `${ENV_VAR}` syntax.

### Supported Providers

| Provider | Base URL | Protocol |
|----------|----------|----------|
| **Anthropic** | api.anthropic.com | Native |
| **OpenAI** | api.openai.com/v1 | OpenAI |
| **DeepSeek** | api.deepseek.com/v1 | OpenAI |
| **Xiaomi MiMo** | token-plan-cn.xiaomimimo.com/v1 | OpenAI |
| **Xiaomi MiMo (Anthropic)** | token-plan-cn.xiaomimimo.com/anthropic | Anthropic |
| **OpenRouter** | openrouter.ai/api/v1 | OpenAI |
| **Qwen (DashScope)** | dashscope.aliyuncs.com/compatible-mode/v1 | OpenAI |
| **Zhipu (GLM)** | open.bigmodel.cn/api/paas/v4/ | OpenAI |
| **MiniMax** | api.minimax.chat/v1 | OpenAI |
| **Moonshot (Kimi)** | api.moonshot.cn/v1 | OpenAI |
| **Doubao (ByteDance)** | ark.cn-beijing.volces.com/api/v3 | OpenAI |
| **Baichuan** | api.baichuan-ai.com/v1 | OpenAI |
| **Hunyuan (Tencent)** | api.hunyuan.cloud.tencent.com/v1 | OpenAI |
| **Yi (01.AI)** | api.lingyiwanwu.com/v1 | OpenAI |
| **StepFun** | api.stepfun.com/v1 | OpenAI |
| **Ollama (Local)** | localhost:11434/v1 | OpenAI |

### Task-Model Mapping

Different tasks can use different models:

```yaml
task_models:
  _default:
    provider: anthropic
    model: claude-sonnet-4-20250514
  field_mapping:
    provider: anthropic
    model: claude-sonnet-4-20250514
  relevance_judge:
    provider: openai
    model: gpt-4o-mini
  unit_suggestion:
    provider: deepseek
    model: deepseek-chat
```

### Fallback Chain

When the primary provider fails, the system automatically tries alternatives:

```yaml
fallback_chain:
  - provider: anthropic
    model: claude-sonnet-4-20250514
  - provider: openai
    model: gpt-4o
  - provider: deepseek
    model: deepseek-chat
```

## Project Structure

```
GeoChem Data Curation Agent/
├── config/
│   └── settings.yaml          # Global LLM provider and task config
├── src/geochem/
│   ├── __init__.py
│   ├── cli.py                 # Typer CLI entry point
│   ├── core/
│   │   ├── config.py          # AppConfig, ProviderConfig, load/save
│   │   ├── database.py        # SQLite schema (17 tables)
│   │   ├── exceptions.py      # 18 custom exception types
│   │   ├── logging_config.py  # JSONL + console logging
│   │   ├── memory.py          # MemoryStore (YAML + MD dual format)
│   │   ├── models.py          # 20+ Pydantic data models
│   │   ├── project.py         # ProjectManager (create/load/list)
│   │   └── schema_manager.py  # SchemaManager (match, normalize, alias)
│   ├── providers/
│   │   ├── base.py            # BaseProvider ABC
│   │   ├── registry.py        # ProviderRegistry
│   │   ├── llm_client.py      # LLMClient (routing, fallback, caching)
│   │   ├── openai_provider.py # OpenAI-compatible (12 providers)
│   │   ├── anthropic_provider.py
│   │   └── ollama_provider.py
│   ├── extractors/            # (WP3 - pending)
│   ├── skills/                # (WP4-WP6 - pending)
│   └── ui/                    # (WP7 - pending)
├── tests/
│   ├── fixtures/              # Test data (schema, Excel, CSV, rules)
│   └── unit/                  # 77 unit tests
├── pyproject.toml
└── 开发计划_多Agent分工.md     # Development plan
```

## Database Schema

17 tables with cell-level provenance:

| Table | Purpose |
|-------|---------|
| projects | Project metadata |
| articles | Paper metadata |
| resources | PDF, Excel, supplementary files |
| sections | Paper section structure |
| table_assets | Raw table assets |
| candidate_tables | Extracted candidate tables |
| candidate_columns | Detected columns with unit candidates |
| candidate_rows | Raw data rows |
| field_mappings | Mapping suggestions |
| mapping_rules | Confirmed mapping rules |
| review_items | Human review queue |
| review_decisions | Review outcome records |
| calculation_records | Unit conversion provenance |
| standardized_records | Final standardized data |
| export_jobs | Export operation records |
| llm_calls | LLM token tracking |
| processing_events | Pipeline event log |

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/unit/test_schema_edge_cases.py -v

# Run with coverage
pytest tests/ --cov=geochem --cov-report=term-missing
```

## Development Progress

### Completed

- [x] **WP0**: Test fixtures, schema, golden rules
- [x] **WP1**: Project infrastructure, database, CLI, logging
  - [x] ProjectManager (create/load/list/delete)
  - [x] SchemaManager (YAML schema, aliases, Unicode normalization)
  - [x] SQLite database (17 tables)
  - [x] JSONL logging framework
  - [x] MemoryStore (read-only)
  - [x] Custom exceptions (18 types)
  - [x] LLM multi-provider system:
    - [x] BaseProvider ABC
    - [x] ProviderRegistry
    - [x] OpenAIProvider (OpenAI-compatible, 12 providers)
    - [x] AnthropicProvider
    - [x] OllamaProvider
    - [x] LLMClient (task routing, fallback, caching, token tracking)
    - [x] 16 providers configured (Anthropic, OpenAI, DeepSeek, Xiaomi, OpenRouter, Qwen, Zhipu, MiniMax, Moonshot, Doubao, Baichuan, Hunyuan, Yi, StepFun, Ollama)
    - [x] CLI commands: `llm list`, `llm check`, `llm test`, `llm cost`
  - [x] CLI commands: `new-project`, `list-projects`, `status`, `import-schema`
  - [x] 77 unit tests passing

### Current Work Packages

- [x] **WP2**: Document ingestion and article-scoped resources
- [x] **WP3**: Excel/CSV/PDF candidate extraction
- [x] **WP4**: Header mapping, units and calculation archive
- [x] **WP5**: Review, teaching patches and rule memory
- [x] **WP6**: Standardized export, trace and cost reporting
- [x] **WP7**: Local Web UI foundation and schema-driven agent workbench
- [ ] **WP8**: Desktop packaging, large-document performance and production visual regression

## License

Private - Academic research use.
