# 第一次运行 CareerPilot

准备 Git 和带 Compose v2 的 Docker，Windows 或 macOS 可以使用 Docker Desktop。以下命令可在 PowerShell 或常用 Unix shell 中运行。浏览题库和已有答卷无需模型密钥。[下载 Git](https://git-scm.com/downloads) · [安装 Docker Compose](https://docs.docker.com/compose/install/)

## 启动个人工作空间

在一个新目录打开终端。首次下载代码后，复制本机配置并启动服务。首次构建会下载镜像和依赖。

```shell
git clone https://github.com/Lueyu/CareerPilot.git
cd CareerPilot
cp .env.example .env
docker compose up -d --build --wait
```

仅当这是刚创建的已知新库，或你已在维护窗口确认它是现有正常安装时，登记恢复控制状态。恢复出来的数据库不能运行此步骤。先核对实际库名，再执行；普通启动不会把未知库自动登记为正常。登记后重启服务以启动正常任务。更多背景见[备份与隔离恢复](../deploy/BACKUP_RECOVERY.md)。

```shell
docker compose exec server python provision_recovery_control.py --confirm 'PROVISION KNOWN INSTALLATION careerpilot'
docker compose restart server worker
```

打开 [本机工作空间](http://127.0.0.1:5173/)。顶部语言菜单可选 **简体中文** 或 **English**，学习助手的新回复和面试官的新提问会使用所选语言。

从左侧进入 **考试与面经 → 查看题库**，选一道题点 **查看参考**。在自己的工作空间里，也可以点 **导入面经**，粘贴题目，审核后保存在「我的题目」。

## 打开首页截图里的工作空间

在刚才的目录运行下面的命令。它会添加一个独立的「林舟的求职笔记」工作空间。重复运行会保留你已有的编辑。

```shell
docker compose exec server python -m app.showcase
```

刷新网页，点击左侧工作空间名称，选择 **林舟的求职笔记**。在 **考试与面经 → 查看题库** 打开异步导入服务的第一道题，再去 **学习计划** 找到「补齐同键异参的导入契约」。在工作空间菜单里选择 **个人学习空间**，即可回到自己的记录。

## 让 AI 和你对练

打开 **设置与工作空间** 查看模型配置。使用支持的模型提供商时，可以在本机 `.env` 中填写 `LLM_PROVIDER`、`LLM_BASE_URL`、`LLM_MODEL` 和 `LLM_API_KEY`，再运行 `docker compose up -d --wait` 使容器载入新配置。模型调用可能产生提供商费用，练习内容会发送给所配置的服务。

从一道题旁的 **围绕这个问题学习** 进入对话，试着问「如果同一个请求编号对应两份不同的文件，会发生什么？」。[学习助手操作指南](study-assistant.md)

## 停止、继续和排查

- **结束本次使用**　在仓库目录运行 `docker compose stop`。下次运行 `docker compose start`，再打开本机工作空间。数据保存在 PostgreSQL 的 Docker 卷中。
- **提示端口已占用**　在 `.env` 中调整相应端口并运行 `docker compose up -d --wait`。例如将 `WEB_HOST_PORT` 改为 `127.0.0.1:15173` 后，用 `http://127.0.0.1:15173/` 访问。配置保留 `127.0.0.1` 以供本机使用。
- **页面或服务没起来**　运行 `docker compose ps` 看服务状态，运行 `docker compose logs --tail=60 server web` 查看启动错误。构建阶段下载失败时，先检查 Docker 的网络或代理配置，再重试启动命令。

[在 Windows 分别启动前后端](getting-started-windows.md) · [备份与恢复](../LOCAL_DEPLOYMENT_GUIDE.md) · [配置反馈邮箱](product-feedback.md) · [参与开发](../CONTRIBUTING.md)
