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
- **Web UI**: React workbench backed by FastAPI, with both local preview and managed LAN deployment
- **CLI compatibility**: Existing curation pipeline remains operable from command line

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.10+ |
| Data models | Pydantic 2.x |
| Database | SQLite for local preview; PostgreSQL for shared deployments |
| CLI | Typer + Rich |
| PDF parsing | PyMuPDF, pdfplumber |
| Excel/CSV | openpyxl, pandas |
| LLM SDKs | openai, anthropic |
| Config | PyYAML |
| API and tasks | FastAPI + SSE, Celery + Redis in shared deployments |
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

### 1. Set up API keys for local development

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
export DEEPSEEK_API_KEY="sk-..."
export MIMO_API_KEY="your-mimo-key"
export OPENROUTER_API_KEY="sk-or-..."
# ... other providers as needed
```

### Start the Web application on this Mac

```bash
bash scripts/mac-preview.sh
# Open http://127.0.0.1:8765
```

This entry refreshes the React build first. If the requested port already runs
GeoChem it reuses that preview; if another program owns the port, it selects a
nearby free port. Pin a port with `GEOCHEM_PORT=8878 bash scripts/mac-preview.sh`,
or set `GEOCHEM_SKIP_WEB_BUILD=true` when a frontend rebuild is unnecessary.

The React + FastAPI application is the sole graphical interface. The explicit
`.venv/bin/geochem-web` command starts the same Web service; `geochem-ui` is
kept as a backwards-compatible alias.

### Deploy for a LAN team

The production profile uses PostgreSQL, Redis/Celery, Keycloak, Caddy and
encrypted NAS backups. Build and validate the full Linux image in WSL2 before
deploying it to Ubuntu Server. See [the LAN deployment manual](docs/lan-deployment.md).

```bash
# WSL2 integration host
bash scripts/server-bootstrap.sh
# edit deploy/.env, then:
bash scripts/wsl2-deploy.sh

# Ubuntu production host with NAS mounted
bash scripts/server-deploy.sh --production
sudo bash scripts/install-server-services.sh --start
```

The WSL2 command runs host preflight, image build, business and LangGraph
checkpoint migrations, startup, post-deploy verification and prints the
Administrator PowerShell command needed to trust the local HTTPS certificate.
`scripts/server-acceptance.sh` adds an
authenticated 50-user read-capacity baseline. The final command installs the
systemd application service and nightly NAS backup timer. Docker is not required
for the Mac preview path.

The LAN build includes a Keycloak-backed login page and two separate product
shells. Ordinary users enter the GeoChem workspace. Administrators choose the
workspace or the dark management console after every login. The management
console provides users, fixed roles, encrypted server-side model credentials,
per-user model/token limits, storage quotas, tasks and audit records. Raw model
keys are never returned to browsers. Environment-variable model keys remain an
optional compatibility path, not a LAN setup requirement.

The Mac preview renders both shells in an explicit read-only development mode;
it does not pretend that Keycloak login is active. The first LAN administrator
is generated into the protected `deploy/.env` file and must change the temporary
password on first login. After opening the administrator overview in WSL2, run
`bash scripts/wsl2-login-acceptance.sh` to verify the actual browser login and
governance profile.

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
│   ├── settings.example.yaml    # Config template (secrets via ${ENV} refs)
│   └── skills/                  # Agent behavior specs (tool contracts)
├── src/geochem/
│   ├── __init__.py
│   ├── cli.py                   # Typer CLI entry point
│   ├── agent_service.py         # Retrieval + conversational + curation agents (LangGraph)
│   ├── agent_tools.py           # Agent tool registry (Pydantic-validated inputs)
│   ├── workbench_service.py     # Workbench domain service (elements, candidates, evidence)
│   ├── workflow.py              # WorkflowRunner + EventBus
│   ├── background_tasks.py      # Local thread pool / Celery task dispatch
│   ├── core/
│   │   ├── config.py            # AppConfig, ProviderConfig, load/save
│   │   ├── database.py          # SQLite schema (50+ tables)
│   │   ├── database_backend.py  # PostgreSQL backend
│   │   ├── runtime.py           # Environment validation (dev/staging/production)
│   │   ├── secrets.py           # ${ENV} / OS keychain secret resolution
│   │   ├── exceptions.py        # Custom exception types
│   │   ├── logging_config.py    # JSONL + console logging
│   │   ├── memory.py            # MemoryStore (YAML + MD dual format)
│   │   ├── models.py            # Pydantic data models
│   │   ├── project.py           # ProjectManager (create/load/list)
│   │   └── schema_manager.py    # SchemaManager (match, normalize, alias)
│   ├── providers/
│   │   ├── base.py              # BaseProvider ABC
│   │   ├── registry.py          # ProviderRegistry
│   │   ├── llm_client.py        # LLMClient (routing, fallback, caching)
│   │   ├── openai_provider.py   # OpenAI-compatible (12 providers)
│   │   ├── anthropic_provider.py
│   │   ├── google_provider.py
│   │   ├── zhipu_provider.py
│   │   └── ollama_provider.py
│   ├── extractors/              # CSV / Excel / PDF (docling, pdfplumber, PyMuPDF)
│   ├── ingestion/               # DOI, literature search, file import, web reader
│   ├── curation/                # Mapping engine, standardization, review, teaching
│   ├── services/                # Admin governance (AES-256-GCM credential vault), model access
│   └── web/                     # FastAPI app, OIDC auth + RBAC, Keycloak admin
├── web/                         # React 19 + TypeScript + Vite frontend
├── deploy/                      # docker-compose, Caddy, Keycloak realm, systemd
├── migrations/                  # Alembic PostgreSQL migrations
├── scripts/                     # Bootstrap / deploy / verify / backup / load-test
├── tests/
│   ├── fixtures/                # Test data (schema, Excel, CSV, rules)
│   └── unit/                    # 330+ unit tests
├── Dockerfile
└── pyproject.toml
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
  - [x] Unit test suite (330+ tests)

### Current Work Packages

- [x] **WP2**: Document ingestion and article-scoped resources
- [x] **WP3**: Excel/CSV/PDF candidate extraction
- [x] **WP4**: Header mapping, units and calculation archive
- [x] **WP5**: Review, teaching patches and rule memory
- [x] **WP6**: Standardized export, trace and cost reporting
- [x] **WP7**: Local Web UI foundation and schema-driven agent workbench
- [ ] **WP8**: LAN production acceptance, large-document performance and production visual regression

## License

Private - Academic research use.
