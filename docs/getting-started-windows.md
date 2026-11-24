# 在 Windows 分别启动前后端

这条路径适合修改代码或分开调试前后端。只想体验产品，请先看 [Docker 快速开始](getting-started.md)。浏览本地题库无需注册，也无需配置模型或 GitHub Token。

## 1. 下载代码并启动数据库

准备 Git、Python 3.12、Node.js 24 和已启动的 Docker Desktop。在 PowerShell 中进入一个用于存放这次体验的目录，再运行：

```powershell
git clone https://github.com/Lueyu/CareerPilot.git
cd CareerPilot
docker run -d --name careerpilot-local-db -e POSTGRES_USER=careerpilot -e POSTGRES_PASSWORD=careerpilot_dev -e POSTGRES_DB=careerpilot -p 127.0.0.1:15432:5432 postgres:16-alpine
docker exec careerpilot-local-db pg_isready -U careerpilot -d careerpilot
```

看到 `accepting connections` 后继续。如果数据库正在初始化，等几秒后重试最后一条命令。

## 2. 启动后端

在仓库根目录安装依赖。虚拟环境位于仓库旁边的 `careerpilot-venv` 目录。

```powershell
python -m venv ../careerpilot-venv
& ../careerpilot-venv/Scripts/python.exe -m pip install -r server/requirements.txt
```

在同一个窗口设置本次启动的环境变量。逐条执行初始化命令，每条成功后再执行下一条。

```powershell
$env:DATABASE_URL = 'postgresql+asyncpg://careerpilot:careerpilot_dev@127.0.0.1:15432/careerpilot'
$env:API_TOKEN = 'cp-dev-token'
$env:LLM_PROVIDER = ''
$env:LLM_API_KEY = ''
$env:GITHUB_TOKEN = ''
cd server
& ../../careerpilot-venv/Scripts/python.exe bootstrap.py
& ../../careerpilot-venv/Scripts/python.exe migrate.py
& ../../careerpilot-venv/Scripts/python.exe -m app.seed
& ../../careerpilot-venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 18080
```

保留这个窗口。打开 [后端健康检查](http://127.0.0.1:18080/healthz)，确认返回 `"ok":true`。

## 3. 启动前端并打开一道题

另开一个 PowerShell 窗口并进入仓库根目录。

```powershell
$env:VITE_API_BASE = 'http://127.0.0.1:18080/api/v1'
cd web
npm.cmd ci
npm.cmd run dev -- --host 127.0.0.1 --port 15173 --strictPort
```

打开 [CareerPilot](http://127.0.0.1:15173/)。左侧显示 **个人工作空间**。依次点击 **考试与面经**、**查看题库**，再点击任意题目旁的 **查看参考**，打开参考答案。

如果端口被占用，换一个空闲端口。改数据库端口时同步修改 `DATABASE_URL`，改后端端口时同步修改 `VITE_API_BASE`。首页数据加载失败时，先打开后端健康检查，再核对前端窗口中的 API 地址并重启前端。

## 4. 可选体验林舟的求职笔记

另开一个 PowerShell 窗口，进入仓库根目录。连接同一个本地数据库并写入样例数据。

```powershell
$env:DATABASE_URL = 'postgresql+asyncpg://careerpilot:careerpilot_dev@127.0.0.1:15432/careerpilot'
cd server
& ../../careerpilot-venv/Scripts/python.exe -m app.showcase
```

回到首页，点击左侧的工作空间卡片，在 **切换工作空间** 中选择 **林舟的求职笔记**。该空间包含虚构的求职记录。要回到自己的空间，用同一菜单选择 **个人学习空间**。

## 5. 停止或下次继续

在前后端窗口按 `Ctrl+C`。从任一窗口运行 `docker stop careerpilot-local-db`。下次用 `docker start careerpilot-local-db` 启动同一个数据库容器，再启动前后端。保留容器即可保留这次体验的数据。
