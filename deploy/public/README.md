# CareerPilot 公开种子版运维

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

运行 `python deploy/public/test_release.py --config-only` 检查合成配置。去掉 `--config-only` 会额外构建合成镜像、检查回环 HTTPS 流，并用临时数据库和 age 密钥演练恢复。只验收恢复时用 `--restore-only`。完整检查需要 Linux 或带 Docker 的 WSL，以及 age、age-keygen、OpenSSL 和 Bash。先确保当前 daemon 有 `postgres:16-alpine` 镜像。上传环节使用本地副本。

## 在可信构建机生成镜像

用集成 Git 提交作为镜像标签和运维文件版本。构建上下文分别是 `server/` 和 `web/`。两个 `Dockerfile.seed.dockerignore` 使用允许清单。构建前核对集成后的公开模块和复用依赖均在清单中。检查生成的 API 镜像及前端静态资源，包括 Vite 生成的源码映射。API 镜像运行 `app.public_main:app`。前端最终镜像只复制 `npm run build:seed` 生成的 `dist-seed/`。

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
| `SEED_DB_NAME` | 首次使用 `careerpilot_seed`。恢复切换时使用 `restore.sh --keep` 输出的数据库名 |
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

## 设置每日加密备份并演练恢复

在独立备份机准备权限受限的 SSH 账号和目录，并设定保留期。超过一天未收到新归档时应发出报警。使用备份专用 SSH 公钥。核验备份主机指纹并写入运行用户的 `known_hosts`。ECS 安装 age、rsync、OpenSSL 和 SSH 客户端。在恢复环境用 `age-keygen -o /安全路径/seed-backup.key` 生成密钥，只将公钥给 ECS。

将专用 SSH 私钥设为 `0600`。在 root 的 SSH 配置中为备份主机设置 `IdentityFile` 和 `IdentitiesOnly yes`。在 `/etc/careerpilot-seed/backup.conf` 设置 `SEED_BACKUP_RECIPIENT=age1…` 和 `SEED_BACKUP_TARGET=backup-user@backup-host:/srv/backup/careerpilot-seed/`，权限设为 `0600`。脚本将 PostgreSQL 归档直接送入 age。临时文件和独立备份机上只有加密归档。以 root 安装定时器，检查第一次上传日志。

```bash
cd /opt/careerpilot-seed/current
install -m 0644 careerpilot-seed-backup.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl start careerpilot-seed-backup.service
journalctl -u careerpilot-seed-backup.service -n 20 --no-pager
systemctl enable --now careerpilot-seed-backup.timer
systemctl list-timers careerpilot-seed-backup.timer
```

发布前用至少一个合成账号演练。在隔离恢复环境下载备份机上的可信归档，用同一 PostgreSQL 主版本和新 Compose 项目启动 `db`。恢复脚本生成新数据库，以单事务导入，并校验数据表。`--dry-run` 清理演练数据库。`--keep` 输出库名供继续验收。

```bash
export COMPOSE_PROJECT_NAME=careerpilot-seed-recovery
export SEED_ENV_FILE=/恢复路径/recovery.conf
./compose.sh up --no-build --pull never --wait db
./restore.sh --dry-run /恢复路径/backup.pgdump.age /安全路径/seed-backup.key
./restore.sh --keep /恢复路径/backup.pgdump.age /安全路径/seed-backup.key
```

将隔离配置的 `SEED_DB_NAME` 改成返回的数据库名，再启动隔离 API 和 Web。用合成账号确认原有题目和作答可读。

生产恢复时，在独立的终端进入 `/opt/careerpilot-seed/current`，使用默认生产项目名与配置文件。先执行 `./compose.sh stop api` 停写，再使用已验收归档和 `--keep` 创建恢复库。更新生产 `SEED_DB_NAME`，执行 `./compose.sh up --no-build --pull never --wait` 后验证登录和数据。保留原库，记录新库名和归档时间。此后的备份跟随 Compose 当前选中的数据库。

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
