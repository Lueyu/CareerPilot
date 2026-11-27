# CareerPilot 公开种子版运维

## 在本机启动可登录的完整工作台

原个人实例继续提供 5173 页面和 8000 API。注册入口在 18457 验证登录，已绑定的个人账号使用原有工作空间。其他账号使用各自的数据库、文件卷和后台任务，新账号的题库为空。已保存在注册版的私题和笔记会带入该账号的工作台。

在仓库根目录构建网页和独立工作台镜像。使用已经启动的注册数据库，以及首次部署生成的账号环境文件和运行状态目录。

```powershell
npm.cmd --prefix web ci
npm.cmd --prefix web run build:seed
docker build -f deploy/tenant/Dockerfile -t careerpilot-tenant-native:parity-20261124 .
$accountEnv = Read-Host '现有注册版环境文件路径'
$runtimeState = Read-Host '保存账号绑定和运行密钥的私有目录'
$personalServer = Read-Host '原个人实例的 server 目录'
$ownerName = Read-Host '已核实的个人账号用户名'
& "$personalServer/.venv/Scripts/python.exe" deploy/tenant/start_local.py --account-env $accountEnv --state-dir $runtimeState --personal-server $personalServer --bind-owner $ownerName --use-personal-model --local-unmetered
```

首次绑定会记录该账号和工作空间的数据库标识。以后启动时省略 `--bind-owner`，继续使用同一状态目录；备份时一并保存该目录、注册库和租户卷。例子中的模型选项沿用个人配置并打开本机不限额模式。已有安装如果把注册版 Python 依赖装在单独目录，启动时用 `--library-dir` 指向该目录。打开 [本机注册工作台](http://127.0.0.1:18457/)。

下方 Compose 流程用于注册服务、HTTPS 入口和备份演练。云端完整工作台还需要可用的租户运行环境及独立的数据卷备份。

## 在本机验收

从集成提交的仓库根目录运行。需要 Docker Compose、Bash 和 OpenSSL。Windows 可使用 Git Bash。Compose 使用独立项目和卷，两个端口都绑定到回环地址。模型配置使用不可解析的域名和合成凭据。

```bash
export COMPOSE_PROJECT_NAME=careerpilot-seed-loopback
export SEED_ENV_FILE="$(mktemp)"
bash deploy/public/local-env.sh "$(git rev-parse HEAD)" > "$SEED_ENV_FILE"
bash deploy/public/compose.sh config --quiet
bash deploy/public/compose.sh up --build --wait
bash deploy/public/compose.sh exec -T web \
  cat /data/caddy/pki/authorities/local/root.crt > /tmp/careerpilot-seed-ca.crt
curl --fail --cacert /tmp/careerpilot-seed-ca.crt https://localhost:8443/
```

Caddy 首次签发本机证书后，CA 文件才会出现。浏览器可在隔离的配置中导入该 CA。测试结束后用同一项目名和配置执行 `bash deploy/public/compose.sh down`。该命令保留验收卷。再次启动时继续用原配置文件和密码。要演练新库，换一个 Compose 项目名和配置文件。

运行 `python deploy/public/test_release.py --config-only` 检查合成配置。去掉 `--config-only` 会额外构建合成镜像、检查回环 HTTPS、项目深链接、静态资源和 SSE，并用临时数据库和 age 密钥演练恢复。只验收代理和构建隔离时用 `--proxy-only`。只验收恢复时用 `--restore-only`。完整检查需要 Linux 或带 Docker 的 WSL，以及 age、age-keygen、OpenSSL 和 Bash。先确保当前 daemon 有 `postgres:16-alpine` 镜像。上传环节使用本地副本。

运行 `python deploy/public/test_release.py --build-only` 用当前工作树和正式 Docker 允许清单构建 API、前端构建阶段和最终镜像。检查公开 API 可导入、私有源码未进入构建阶段，以及静态产物没有源码映射。该检查无需启动数据库或读取运行配置。

## 在可信构建机生成镜像

用集成 Git 提交作为镜像标签和运维文件版本。构建上下文分别是 `server/` 和 `web/`。两个 `Dockerfile.seed.dockerignore` 使用允许清单。构建前核对集成后的公开模块和复用依赖均在清单中。公开前端由根目录的 `web/index.html` 和 `web/src/main.tsx` 启动，Vite 将工作区入口指向 `RegisteredApp.tsx`，登录后复用 `src/App.tsx` 的原生页面。公开构建排除本地 API 传输模块和原题库数据。静态产物位于 `/static-assets/`，`/assets` 和 `/assets/project/:id` 由应用路由处理。源码映射关闭。API 镜像运行 `app.public_main:app`。前端最终镜像只复制 `npm run build:seed` 生成的 `dist-seed/`。

```bash
release="$(git rev-parse HEAD)"
export SEED_ENV_FILE="$(mktemp)"
bash deploy/public/local-env.sh "$release" > "$SEED_ENV_FILE"
bash deploy/public/compose.sh config --quiet
bash deploy/public/compose.sh build api web
docker image save -o "careerpilot-seed-$release.tar" \
  "careerpilot-seed-api:$release" "careerpilot-seed-web:$release"
sha256sum "careerpilot-seed-$release.tar" > "careerpilot-seed-$release.tar.sha256"
```

构建机可通过 `SEED_PYTHON_BASE`、`SEED_NODE_BASE` 和 `SEED_CADDY_BASE` 指定可达的镜像仓库和已验证的 digest。用 `SEED_PIP_INDEX_URL` 和 `SEED_NPM_REGISTRY` 指定可信 HTTPS 镜像源。构建参数只接收公开 URL。若 ECS 无法拉取 PostgreSQL，在可信构建机获取与 ECS 架构匹配的 `postgres:16-alpine`，以同样方式打包并记录 digest。将镜像包、校验文件和 `deploy/public/` 运维文件传至 ECS。构建机需与 ECS 使用同一 CPU 架构，或由 Buildx 显式构建该架构。

## 配置独立的阿里云 Linux 主机

首选上海，首次可用 2 核 4 GB 验收。内地上线前核实域名备案和阿里云接入。公网安全组开放 TCP 80/443，SSH 只允许管理来源。将 `SEED_HOST` 的 DNS A 记录指向 ECS。有 AAAA 记录时也要验证 IPv6。Caddy 依赖公网可达的域名签发和续期证书。Compose 仅发布 Caddy 端口。PostgreSQL 使用内部网络和新卷。API 在内部端口运行，通过前端所在网络访问模型供应商。

安装 Docker Engine 和 Compose V2。首次上架前先在隔离机器或回环环境演练以下操作。以 root 创建配置目录、安装镜像。

```bash
install -d -m 0700 /etc/careerpilot-seed
install -d -m 0755 /opt/careerpilot-seed/releases
release=填写集成提交哈希
ops=/opt/careerpilot-seed/releases/$release
# 将本提交的 deploy/public/ 运维文件放到 "$ops"。
chmod 0755 "$ops/"*.sh
install -m 0600 "$ops/seed.env.example" /etc/careerpilot-seed/runtime.conf
sha256sum -c "careerpilot-seed-$release.tar.sha256"
docker image load -i "careerpilot-seed-$release.tar"
# 预拉取或导入配置指定的 PostgreSQL 16 镜像。
```

编辑 `/etc/careerpilot-seed/runtime.conf`。发布命令显式读取该文件。`compose config --quiet` 检查配置。直接调用无参数的 `compose config` 会输出解析后的凭据。

| 变量 | 取值 |
| --- | --- |
| `SEED_RELEASE_TAG` | 构建时使用的完整 Git 提交 |
| `SEED_HOST`、`SEED_PUBLIC_ORIGIN` | 裸域名及浏览器使用的完整 HTTPS 来源，例如 `app.agent-interview.icu` 和 `https://app.agent-interview.icu` |
| `SEED_DB_NAME` | 首次使用 `careerpilot_seed`。恢复目标与后续切换须按统一隔离恢复计划单独审核；旧 `restore.sh --keep` 已禁用，不自动更新此值 |
| `SEED_DB_ADMIN_PASSWORD`、`SEED_DB_PASSWORD` | 各用一次 `openssl rand -hex 32` 生成 64 位十六进制密码，分别供 `seed_admin` 和 `seed_app` 使用 |
| `SEED_SESSION_SECRET` | 单独使用 `openssl rand -hex 32` 生成 |
| `SEED_MODEL_BASE_URL`、`SEED_MODEL_KEY`、`SEED_MODEL_NAME` | 实际模型的 HTTPS API 基址、专用 key 和模型名。基址与模型模块约定一致 |
| `SEED_INPUT_CNY_PER_MILLION`、`SEED_OUTPUT_CNY_PER_MILLION` | 每百万输入、输出 token 的实际人民币单价 |
| `SEED_MONTHLY_CNY`、`SEED_DAILY_USER_CNY` | 站点月额度与每用户日额度。示例月额度为 50 元 |
| `SEED_STORAGE_REGION`、`SEED_MODEL_PROVIDER_LABEL` | 上传提示中使用的真实存储地域和供应商名称 |
| `SEED_POSTGRES_IMAGE` | 默认 `postgres:16-alpine`。记录并使用验收过的镜像 digest |

确认 ECS 上没有同名项目。Compose 固定生产项目名 `careerpilot-seed`，因此版本切换继续使用同一个专用 PostgreSQL 卷。初始化脚本建立数据库和应用角色。应用启动创建公开版表。

```bash
ln -sfnT "$ops" /opt/careerpilot-seed/current
cd /opt/careerpilot-seed/current
./compose.sh config --quiet
./compose.sh up --no-build --pull never --wait
./compose.sh ps
curl --fail https://app.agent-interview.icu/
```

在大陆固网和蜂窝网用两个合成账号验收首页、注册、上传、模型首段与数据隔离。记录提交、域名、地域、供应商、失败数和计时。用浏览器 Network 或 `curl --no-buffer` 检查流式接口。Caddy 保留 API 路径并立即转发 `text/event-stream`。[公开种子版验收标准](../../docs/superpowers/specs/2026-11-23-public-seed-beta-design.md)

## 加密备份与隔离恢复

原 `backup.sh`/`restore.sh` 已停用并退出78，不能继续使用旧定时器或 `--keep` 做恢复。原上传失败删除唯一暂存密文、恢复只数表且未隔离认证/预算的路径不再执行。不要启用附带的旧 `careerpilot-seed-backup.timer`。已有调度应由操作员在维护窗口停用并替换，本次没有修改真实调度。

使用统一的[加密备份与隔离恢复操作手册](../BACKUP_RECOVERY.md)。在具有 PostgreSQL 维护网络权限的主机安装 PowerShell 7、原生 PG 工具、age 和项目 Python 依赖，再从仓库根目录执行 deploy/backup.ps1、deploy/restore.ps1。通过受保护 PG* 环境连接专用数据库，不公开数据库到互联网。需要 Docker 隔离网络时由操作员准备专用维护容器/端口，不在脚本中修改真实容器或 ACL。

完整 bundle 包含加密 dump、加密元数据和摘要清单。旧单文件 `.pgdump.age` 不满足新契约，需要受信任的离线隔离转换并重新备份，不能直接宣称验证成功。恢复目标始终是全新未路由备用库，不自动更新 SEED_DB_NAME、不启动线上应用、不清理原库。恢复后所有业务 API、登录和付费仍隔离；H1 凭据代际核对和独立费用对账完成前不能切换。

新部署或已知正常安装须在具有完整同版本源码和项目Python依赖的维护主机离线登记，不能假设精简公开镜像内包含维护CLI。先通过受保护PG*环境连接已核验的新库/正常原实例，只执行定向控制表迁移，再在server目录用受保护DATABASE_URL执行登记。恢复库不能登记。

```shell
psql --no-psqlrc --set ON_ERROR_STOP=1 --file server/migrations/20261128_recovery_control.sql
cd server
python provision_recovery_control.py --confirm 'PROVISION KNOWN INSTALLATION careerpilot_seed'
```

命令中的数据库名必须与已审核的专用数据库一致，PG*和DATABASE_URL也须指向同一目标。登记后仅由操作员在已知安装维护窗口重新启动消费者；不用于解除恢复隔离。公开镜像本项只补入新recovery_guard/recovery_control模块的精确allowlist，未声称完整注册版镜像import已通过；既有rate_limits和私有ops/telemetry导入的allowlist缺口另有边界。没有已授权异地目标时状态为 NOT_CONFIGURED。本次只验证本机合成数据库，真实异地副本与生产服务 RTO 未验证。

## 回滚应用版本

回滚前核对上一版本与当前数据库结构兼容，并保留加密归档。保留上一版本镜像和运维目录。把 `runtime.conf` 的 `SEED_RELEASE_TAG` 改回上一个提交，更新 `current` 链接并重建 API 和 Web。

```bash
previous=填写上一个可用提交哈希
ln -sfnT "/opt/careerpilot-seed/releases/$previous" /opt/careerpilot-seed/current
cd /opt/careerpilot-seed/current
./compose.sh config --quiet
./compose.sh up -d --no-build --pull never --no-deps --force-recreate --wait api web
./compose.sh ps
curl --fail https://app.agent-interview.icu/
```

版本回滚复用当前库、会话密钥和 Caddy 证书卷。数据库结构不兼容时，按上一节恢复到新库，记录备份时间之后需要核对的写入。
