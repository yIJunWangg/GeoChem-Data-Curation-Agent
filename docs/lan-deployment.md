# GeoChem 局域网生产部署手册

本手册对应第一版组织工作区部署：20–50 个账号通过浏览器访问一套 GeoChem，
PostgreSQL 保存业务数据，Redis/Celery 执行耗时任务，Keycloak 管理身份和角色，
Caddy 提供局域网 HTTPS，文章文件保存在服务器本地 SSD，并使用 restic 加密备份到 NAS。

## 1. 运行拓扑

```text
LAN browser
    |
    | HTTPS 443
    v
  Caddy --------------------> Keycloak (internal 8080)
    |
    +-----------------------> FastAPI + React (internal 8765)
                                  |
                   +--------------+--------------+
                   |              |              |
               PostgreSQL       Redis       Celery worker
                   |                             |
                   +--------- local SSD ---------+
                                  |
                             restic -> NAS
```

只有 Caddy 发布 `80/443`。PostgreSQL、Redis、Keycloak 和 FastAPI 均只存在于
Docker 内部网络。迁移后的既有数据继续位于 `DEFAULT_WORKSPACE`，并归入默认组织；
Keycloak 负责登录身份和平台管理员，GeoChem 数据库使用工作区成员关系区分 Owner、
Curator、Reviewer 和 Viewer。所有文章、PDF、对话、Agent、RAG 和导出访问都必须通过
工作区成员校验，不能只凭对象 ID 读取。

FastAPI 只接受 Caddy 从私有 Compose 网络转发的客户端地址信息。生产环境关闭
`/docs`、`/redoc` 和 `/openapi.json`；Mac 开发预览仍保留这些接口，便于本机调试。
`GEOCHEM_FORWARDED_ALLOW_IPS=*` 只在 Web 容器没有宿主机端口、唯一入口为 Caddy 时安全，
不得在把 `8765` 直接发布到局域网的同时沿用该配置。

## 2. 机器分工

- **当前 Mac**：继续运行 `scripts/mac-preview.sh`，用于日常开发和视觉预览；不要求
  启动整套生产容器。
- **Windows + WSL2**：用于第一次完整镜像构建、Compose 联调和迁移演练。它与最终
  Ubuntu 服务器同为 Linux/x86 环境，可避免 Apple Silicon 跨架构镜像差异。
- **Ubuntu Server**：正式局域网服务。推荐 8 核以上、32 GB 内存、100 GB 以上 SSD。
  RTX 3070 暂不参与本版推理；当前 LLM 使用外部 API，Docling 默认由 CPU worker 运行。

Mac M5 16 GB 可以构建镜像，但 Docling 及其机器学习依赖会消耗较多内存、磁盘和下载
时间。生产镜像直接在 WSL2/Ubuntu 构建更稳定，也不需要维护 arm64 与 amd64 两套产物。

## 3. WSL2 联调

1. 安装 Ubuntu WSL2、Docker Desktop 的 WSL integration，以及 Git。
2. 把仓库放在 WSL2 Linux 文件系统中，例如 `/opt/geochem`，不要放在 `/mnt/c`。
3. 生成部署环境、基础密码和首次应用管理员的一次性密码：

   ```bash
   cd /opt/geochem
   bash scripts/server-bootstrap.sh
   ```

4. 检查 `deploy/.env` 中的域名、数据目录和初始管理员。LLM key 可留空；首次登录后可在
   管理后台创建加密模型凭据。不要提交此文件。
5. 运行 WSL2 一键联调入口。它会执行主机预检、镜像构建、Alembic 与 LangGraph
   checkpoint 建表、Keycloak 配置、启动和全链路验收：

   ```bash
   bash scripts/wsl2-deploy.sh
   ```

   任一环节失败都会返回非零状态，不会把“容器已启动”误当成可用。成功后会生成
   `deploy/wsl2-acceptance-report.txt`，并打印下一步需要执行的 Windows 命令。

6. 在 Windows **管理员 PowerShell** 中运行脚本打印的命令。该命令会同时配置 hosts、
   安装 Caddy 根证书到“受信任的根证书颁发机构”并刷新 DNS。例如：

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "...\install-geochem-lan.ps1" `
     -CertificatePath "...\geochem-lan-root.crt"
   ```

7. 访问 `https://geochem.lan`。首次账号由 `GEOCHEM_INITIAL_ADMIN_USERNAME` 指定，
   一次性密码仅保存在权限为 `600` 的 `deploy/.env` 中；首次登录必须修改密码。管理员
   登录后会看到「进入 GeoChem」和「进入管理后台」两个入口。选择管理后台并等待概览
   加载，然后在 WSL2 中运行：

   ```bash
   bash scripts/wsl2-login-acceptance.sh
   ```

   只有该脚本通过，才表示浏览器登录、管理员角色、后台 API、用户档案和默认存储配额
   已经形成真实闭环。`wsl2-deploy.sh` 本身只代表基础设施联调通过。

## 4. Ubuntu Server 首次上线

### 4.1 主机准备

1. 安装 Ubuntu Server 24.04 LTS、Docker Engine、Docker Compose v2、restic、rsync。
2. 将仓库部署到 `/opt/geochem`。
3. 将 NAS 挂载到 `/mnt/nas`，并在 `/etc/fstab` 中设置开机挂载和网络失败重试。
4. 防火墙只允许局域网访问 SSH、HTTP 和 HTTPS；不要开放 5432、6379、8080、8765。
5. 执行：

   ```bash
   cd /opt/geochem
   sudo bash scripts/server-bootstrap.sh
   sudoedit deploy/.env
   sudo bash scripts/server-preflight.sh --production --require-backup
   sudo bash scripts/server-deploy.sh --production
   sudo bash scripts/install-server-services.sh --start
   ```

   `--production` 会强制检查 24 GiB 内存、80 GiB 可用磁盘、凭据加密主密钥、NAS
   挂载和 restic 密码文件。正式主机建议仍按 8 核、32 GB、100 GB 以上 SSD 配置。
   最后一条命令会安装并启用 `geochem.service` 与 `geochem-backup.timer`，使应用在主机
   重启后自动恢复，并在每天夜间执行 NAS 备份。安装脚本会把仓库、Compose、环境文件
   和 NAS 的实际绝对路径写入 systemd unit，不依赖仓库必须位于固定目录。

   首次部署会创建 `GEOCHEM_INITIAL_ADMIN_USERNAME` 指定的应用管理员，并设置
   `GEOCHEM_INITIAL_ADMIN_PASSWORD` 为一次性密码。部署脚本对已有同名用户绝不重置密码，
   因而版本升级不会锁掉现有管理员。Keycloak `master` realm 的 bootstrap 管理员仅用于
   身份服务维护，不是 GeoChem 日常登录账号。

### 4.2 局域网名称与 HTTPS

在路由器或内部 DNS 中增加两条 A 记录，均指向 Ubuntu Server：

```text
geochem.lan       -> SERVER_LAN_IP
auth.geochem.lan  -> SERVER_LAN_IP
```

没有内部 DNS 时，可临时修改每台客户端的 hosts。部署脚本会把自定义域名同步到
Keycloak 的回调地址和 Web Origin。运行 `scripts/export-caddy-ca.sh`，将根证书分发并
安装到每台受控客户端。若未来开放公网，
请改用正式域名和公共 CA，不再使用 `tls internal`。

### 4.3 创建用户和角色

1. 打开 `https://geochem.lan`，使用 `deploy/.env` 中的首次应用管理员账号登录；首次
   登录按提示修改一次性密码。
2. 在登录后的入口选择「进入管理后台」，打开「用户与权限」。
3. 创建用户并设置临时密码。
4. Keycloak 中普通账号至少保留 `viewer` 平台角色；只有需要进入管理后台、管理身份和
   全局配置的账号才授予 `admin`。业务数据权限不在 Keycloak 中分配。
5. 在管理后台打开「工作区成员」，把账号加入目标工作区并分配一个成员角色：
   - `owner`：管理工作区成员与配置，并拥有该工作区全部业务权限。
   - `curator`：文献导入、资源发现、抽取、映射和候选编辑。
   - `reviewer`：审核、正式标准化和导出。
   - `viewer`：只读检索、统计和溯源。
6. 至少保留两个平台管理员和两个默认工作区 Owner，首次登录后修改临时密码。

「用户与权限」还可以停用账号、重置临时密码和撤销当前登录会话。它通过
`geochem-admin-api` 最小权限服务账号访问 Keycloak，仅授予查询、查看和管理用户权限，
不授予 `realm-admin`。`https://auth.geochem.lan` 的 Keycloak 管理控制台只作为身份服务
维护和故障恢复入口，日常账号操作不需要离开 GeoChem。

管理后台的「模型与凭据」用于保存服务器共享模型 Key。Key 使用 AES-256-GCM 加密，
加密主密钥由 Docker secret 提供；浏览器和 API 均只能看到指纹。创建凭据后，在用户详情
中为用户启用对应模型，并按需设置月 Token/费用上限。模型凭据未分配给某个用户时，该
用户不能调用该模型。

管理后台的「存储与配额」显示每个用户的使用量。首次登录自动创建 10 GiB 默认配额；
管理员可按账号调整。文章及附件按导入者/所有者计费，超过配额的上传会在写盘前被拒绝。

生产环境默认按用户限制每分钟 300 次 API 请求、40 次对话/Agent 请求、每小时 20 次
上传，以及同时 3 个耗时任务。对应配置为 `GEOCHEM_REQUEST_RATE_PER_MINUTE`、
`GEOCHEM_CHAT_RATE_PER_MINUTE`、`GEOCHEM_UPLOAD_RATE_PER_HOUR` 和
`GEOCHEM_MAX_CONCURRENT_TASKS_PER_USER`。限流状态存放在 Redis；生产环境 Redis
不可用时受保护请求会明确返回服务不可用，而不会静默放开限制。

Mac 本地预览默认不启用 OIDC，页面会明确显示「本地开发模式」并禁用用户写操作；
这不是生产认证故障。只有局域网 Compose 配置中的 Keycloak 启动后，登录页和真实用户
管理操作才会启用。

## 5. 从 Mac 迁移现有工作区

先在 WSL2 演练一次，再对正式服务器执行。迁移期间停止 Mac 本地写入。

1. 在服务器只启动基础服务并创建数据库结构：

   ```bash
   docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d postgres redis keycloak
   docker compose --env-file deploy/.env -f deploy/docker-compose.yml run --rm migrate
   ```

2. 将 Mac 的 `geochem-data/DEFAULT_WORKSPACE` 复制到服务器
   `/srv/geochem/data/workspaces/DEFAULT_WORKSPACE`。保留文章目录、PDF、表头、计算档案和
   `project.yaml`；`geochem.db` 仅作为迁移源保留。
3. 把 `config/settings.local.yaml` 复制到
   `/srv/geochem/data/config/settings.local.yaml`。文件内只能存在环境变量引用，不得有明文 key。
4. 导入 SQLite 业务数据：

   ```bash
   set -a
   source deploy/.env
   set +a
   docker compose --env-file deploy/.env -f deploy/docker-compose.yml run --rm \
     --entrypoint geochem-migrate-postgres migrate \
     /data/workspaces/DEFAULT_WORKSPACE/geochem.db \
     --database-url "postgresql+psycopg://geochem:${GEOCHEM_DB_PASSWORD}@postgres:5432/geochem"
   ```

5. 启动完整服务并核对文章数、候选行数、标准记录、审核状态和溯源：

   ```bash
   docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d
   curl --cacert deploy/geochem-lan-root.crt https://geochem.lan/api/v1/health
   ```

## 6. 日常运维

### 更新

```bash
cd /opt/geochem
git pull --ff-only
docker compose --env-file deploy/.env -f deploy/docker-compose.yml build
docker compose --env-file deploy/.env -f deploy/docker-compose.yml run --rm migrate
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d
```

更新前先执行一次备份。生产分支应使用固定 Git tag，不要直接部署未验证的工作树。
`server-deploy.sh --production` 检测到已有 PostgreSQL 容器时会自动执行一次完整备份，
随后优雅停止 Caddy、Web 和 Worker，在维护窗口内运行 Alembic，再启动新版本。首次安装
不会触发预升级备份。只有故障抢修且已有外部可验证备份时，才可临时设置
`GEOCHEM_SKIP_PREDEPLOY_BACKUP=true`；这种部署必须在事后补做备份与恢复演练。

### 查看状态

```bash
systemctl status geochem.service
systemctl status geochem-backup.timer
docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps
docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs --tail=200 web worker
bash scripts/server-verify.sh
```

`server-verify.sh` 会检查：容器健康、宿主机端口暴露、PostgreSQL checksum、Alembic
版本与 LangGraph checkpoint 表、Redis AOF、Celery control ping、真实任务队列与结果后端往返、共享存储写权限、容器重启
策略、日志轮转、Web/Worker 信号处理、生产 API 文档关闭、Keycloak 应用管理员、HTTPS、
未登录 API 拒绝和可选的登录 token。
它只读业务数据，可以在每次更新后重复运行。

`server-verify.sh` 不会伪造浏览器登录。WSL2 或新服务器完成首次管理员登录并打开管理
概览后，应额外运行 `scripts/wsl2-login-acceptance.sh`。该脚本从审计表确认同一 admin
账号成功访问 `/api/v1/auth/me` 和 `/api/v1/admin/overview`，并检查用户档案与配额已创建。

### 日志和优雅停机

所有生产容器默认使用 Docker `json-file` 日志驱动，每个容器最多保留
`GEOCHEM_LOG_MAX_FILES=5` 个、每个 `GEOCHEM_LOG_MAX_SIZE=20m` 的日志文件，防止长期运行
把系统盘写满。需要扩大日志窗口时应同时核算磁盘空间，不要关闭轮转。

Web 和 Celery Worker 启用了 Docker init。Web 停机最多等待 1 分钟，Worker 最多等待
10 分钟，让正在执行的 PDF/Docling 任务有机会完成或被 Celery 正常回收。生产更新使用
`server-deploy.sh`，不要直接 `docker kill`；确需强制终止时，应在任务页面确认没有正在
写入的抽取或标准化任务。

### Worker 容量

默认 Web worker 数为 2，Celery worker 并发为 2，适合 32 GB 服务器上运行较重的
PDF/Docling 任务。先观察内存，确认单任务峰值后再提高
`GEOCHEM_WORKER_CONCURRENCY`。Web 服务本身不执行生产后台任务。

默认后台任务软限制为 6900 秒、硬限制为 7200 秒，Redis 可见性超时为 7500 秒，任务
结果保留 24 小时。可见性超时必须始终大于硬限制，否则长任务可能在完成前被重复投递。
Worker 使用晚确认、单任务预取和丢失重排队；调整这些参数后必须重新运行容量测试和
一次 Worker 强制重启恢复演练。

每个 Web/Worker 进程默认最多使用 `GEOCHEM_DB_POOL_SIZE=5` 个常驻连接和
`GEOCHEM_DB_MAX_OVERFLOW=5` 个短时溢出连接。按默认 2 个 Web worker 与 2 个 Celery
子进程，GeoChem 的理论峰值为 40 个连接，给 Keycloak 和运维查询留出 PostgreSQL
连接余量。不要只提高 worker 数而不同时核算连接池、内存和 PostgreSQL
`max_connections`。

### 数据库迁移纪律

`20260718_0001` 是首次服务器部署的基线迁移。生产数据库一旦建立，不得回改该迁移；
后续表、字段和索引变化必须新增 Alembic revision，并先在 WSL2 的数据库副本上验证
升级和恢复。应用启动不会替代 `migrate` 服务，所有上线更新必须先备份，再执行
`alembic upgrade head` 和 `geochem-checkpoint-setup`。服务器配置固定使用
`GEOCHEM_POSTGRES_SCHEMA_MODE=alembic`；Web
和 Worker 启动时只验证迁移标记和核心表，不会在多进程运行期间自动执行 DDL。Mac 的
SQLite 开发模式仍会自动初始化本地数据库。

### 20–50 人容量基线

先以 viewer 账号登录，在浏览器开发者工具的 Local Storage 中找到当前
`oidc.user:` 记录，仅临时取出其中 `access_token`。不要保存到仓库或 `deploy/.env`：

```bash
export GEOCHEM_TEST_ACCESS_TOKEN='temporary-viewer-token'
GEOCHEM_LOAD_USERS=20 GEOCHEM_LOAD_DURATION=60 bash scripts/server-capacity-test.sh
unset GEOCHEM_TEST_ACCESS_TOKEN
```

脚本只访问 dashboard、文章列表和表头列表，不产生写入。默认门槛为错误率不超过 1%、
P95 不超过 2 秒，报告写入被 Git 忽略的 `deploy/capacity-report.json`。没有 token 时脚本
只测试公开健康端点，并明确标记为 `public-edge-only`，不能作为业务容量验收。

## 7. NAS 备份与恢复

备份脚本先生成 `geochem` 与 `keycloak` 两个 PostgreSQL 一致性 dump，再用 restic 加密
备份应用文件和 Caddy 证书。它不会复制运行中的 PostgreSQL 数据目录。

应用开机启动和备份定时器由同一个安装入口管理：

```bash
sudo bash scripts/install-server-services.sh --start
sudo systemctl status geochem.service
sudo systemctl status geochem-backup.timer
sudo systemctl list-timers geochem-backup.timer
sudo systemctl start geochem-backup.service
sudo journalctl -u geochem-backup.service -n 100
```

每次移动仓库、环境文件或 NAS 挂载点后都要重新运行安装脚本，使 unit 内的绝对路径与
当前部署一致。日常版本更新仍使用 `scripts/server-deploy.sh --production`；也可以运行
`sudo systemctl reload geochem.service` 调用同一套受控部署流程。不要直接编辑生成后的
`/etc/systemd/system/geochem*.service`，应修改仓库模板并重新安装。

恢复必须在维护窗口进行，并要求明确确认：

```bash
sudo CONFIRM_RESTORE=YES RESTIC_SNAPSHOT=latest scripts/restore-server.sh
```

每季度至少在独立 WSL2/测试机做一次恢复演练。没有验证过恢复的备份不算可靠备份。

## 8. Mac 本地效果预览

本地开发完全不依赖 Docker、PostgreSQL、Redis 或 Keycloak：

```bash
cd "/Users/wanghaha/vscode_workspace/macos app/GeoChem Data Curation Agent"
bash scripts/mac-preview.sh
```

默认打开 `http://127.0.0.1:8765`，继续使用 SQLite、本地后台线程和禁用认证的 development
profile。启动脚本会计算当前代码构建指纹；若该端口是旧 Python 进程，它不会把旧实例误报为
最新版本，而会选择空闲端口并在终端打印新 URL、构建编号和运行模式。若要显式指定端口：

```bash
GEOCHEM_PORT=8766 bash scripts/mac-preview.sh
```

这条入口必须长期保留，用于验证前端效果和单机功能；服务器配置不会覆盖本地配置。Mac
预览中的「用户管理」会以只读方式展示生产界面和运行状态，但不会伪造登录或真实用户写入；
Keycloak 登录、角色和账号增删只有在局域网 Compose 生产栈中启用。

## 9. 上线验收清单

在正式 Ubuntu 主机上，使用临时 viewer token 执行统一验收入口：

```bash
export GEOCHEM_TEST_ACCESS_TOKEN='temporary-viewer-token'
sudo -E GEOCHEM_LOAD_USERS=50 GEOCHEM_LOAD_DURATION=120 \
  bash scripts/server-acceptance.sh
unset GEOCHEM_TEST_ACCESS_TOKEN
```

该入口按顺序执行生产主机预检、运行栈全链路验证、一次 PostgreSQL + restic 真实备份，
以及 50 用户只读容量测试，并要求 `geochem.service` 和 `geochem-backup.timer` 已启用且
正在运行。只有在调试特殊故障时才可临时设置
`GEOCHEM_ACCEPTANCE_SKIP_BACKUP=true`；使用该开关的结果不算完整生产验收。它通过后，
仍需完成下面的角色、迁移恢复和浏览器人工验收。每次执行都会在
`deploy/acceptance/<UTC 时间>/` 写入 `summary.md`、`summary.json`、各阶段日志和容量 JSON；
即使中途失败也会保留失败步骤。跳过备份时状态为 `partial` 且返回码为 `3`，不得标记上线通过。

- [ ] 客户端通过 HTTPS 访问，无证书警告。
- [ ] viewer、curator、reviewer、admin 权限分别验证。
- [ ] 两个工作区的测试用户不能互相读取文章、PDF、对话、Agent run、RAG 引用或导出文件。
- [ ] 工作区 Owner 可以邀请、停用和调整成员角色，且不能删除最后一个 Owner。
- [ ] `wsl2-login-acceptance.sh` 验证真实管理员登录和管理后台访问通过。
- [ ] 管理员创建一条加密模型凭据并分配给测试用户；API 响应中只显示指纹。
- [ ] 测试用户只能调用被分配的模型，Token/费用限制和存储配额能够阻断超限操作。
- [ ] PostgreSQL、Redis、Keycloak、Web、Worker 均无宿主机直通端口。
- [ ] 所有运行容器均为 `unless-stopped`，日志轮转和 Web/Worker init 验证通过。
- [ ] `/openapi.json` 在生产环境返回 404，FastAPI 只通过 Caddy 接收请求。
- [ ] 500 MB PDF 上传时不会使 Web 进程内存暴涨。
- [ ] 伪造扩展名、MIME 或文件签名的上传被拒绝；上传、对话和普通 API 限流可阻断超限请求。
- [ ] 单用户超过耗时任务并发上限时得到明确提示，其他用户仍可正常提交任务。
- [ ] 同一文章重复任务被幂等限制，失败任务可重试。
- [ ] 重启服务后 Agent checkpoint 和后台任务状态可恢复。
- [ ] Mac 数据迁移后的文章、资源、候选、审核、标准记录和溯源数量一致。
- [ ] NAS 断开时备份明确失败，不误写本地根目录。
- [ ] 从 restic 恢复到测试机成功。
- [ ] LLM key 仅存在于服务器环境或密钥系统中，仓库与 API 响应中无明文。

## 10. 部署脚本速查

| 脚本 | 用途 | 是否修改业务数据 |
|---|---|---|
| `server-bootstrap.sh` | 生成本机 `.env`、目录和基础密钥 | 否 |
| `wsl2-deploy.sh` | WSL2 一键构建、启动、验证并生成 Windows 安装命令 | 仅数据库迁移 |
| `wsl2-login-acceptance.sh` | 验证真实浏览器管理员登录、后台访问、档案与配额 | 否 |
| `server-preflight.sh` | 主机、密钥、端口、存储、NAS 和 Compose 预检 | 否 |
| `server-deploy.sh` | 构建、迁移、启动并自动验证 | 仅数据库迁移 |
| `server-verify.sh` | 运行服务全链路健康和安全边界验证 | 否 |
| `server-capacity-test.sh` | 20–50 用户只读容量基线 | 否 |
| `server-acceptance.sh` | 正式主机统一验收入口（含一次真实备份） | 只写备份 |
| `install-server-services.sh` | 安装开机启动服务与 NAS 备份定时器 | 否 |
| `backup-server.sh` | PostgreSQL dump + restic 加密备份 | 只写备份 |
| `restore-server.sh` | 维护窗口恢复 | 是，要求显式确认 |
| `mac-preview.sh` | Mac SQLite 本地开发预览 | 使用本地开发数据 |
