# 在 Windows 分别启动前后端

准备Git、Python3.12、Node.js和Docker Desktop。本路径只创建全新体验数据库；保留已有个人.env、PG角色、密码和数据。[Docker安装指南](getting-started.md)

## 1. 安装依赖并生成私有配置

在新目录运行。虚拟环境位于仓库旁边，不覆盖原安装：

```powershell
git clone https://github.com/Lueyu/CareerPilot.git
cd CareerPilot
python -m venv ../careerpilot-venv
$python = (Resolve-Path ../careerpilot-venv/Scripts/python.exe).Path
& $python -m pip install -r server/requirements.txt
& $python server/personal_credentials.py init --output personal/runtime/personal.env --port 15432
$privateEnv = (Resolve-Path personal/runtime/personal.env).Path
```

初始化只创建缺失文件，随机生成API令牌、PG密码和G5主密钥；重跑不覆盖、不换令牌。Windows写秘密前设置并回读仅当前SID的受保护DACL；无法确认权限就停止。.env.example仅为变量说明，不能复制后直接启动。旧弱凭据由操作者在维护窗口迁移，不靠debug绕过。

## 2. 创建全新数据库

下面的容器名必须尚不存在。不要把新密码用于旧PG卷。密码从私有文件进入进程环境，不放进命令参数或控制台输出：

```powershell
$values = @{}
Get-Content -LiteralPath $privateEnv | ForEach-Object {
    $pair = $_.Split('=', 2)
    $values[$pair[0]] = $pair[1]
}
$env:POSTGRES_PASSWORD = $values['POSTGRES_PASSWORD']
docker run -d --name careerpilot-local-db -e POSTGRES_USER=careerpilot -e POSTGRES_PASSWORD -e POSTGRES_DB=careerpilot -p 127.0.0.1:15432:5432 postgres:16-alpine
$env:POSTGRES_PASSWORD = $null
docker exec careerpilot-local-db pg_isready -U careerpilot -d careerpilot
```

看到accepting connections后继续。该流程不ALTER任何既有本机PG角色。

## 3. 初始化并显式登记已知新安装

先确认当前窗口没有其他安装遗留的DATABASE_URL/API_TOKEN，因为进程环境变量优先于文件。每条成功后继续下一条。本节新库可以登记，恢复数据库禁止登记：

```powershell
$env:CAREERPILOT_ENV_FILE = $privateEnv
$env:LLM_PROVIDER = ''
$env:LLM_API_KEY = ''
$env:GITHUB_TOKEN = ''
Set-Location server
& $python personal_credentials.py check
& $python bootstrap.py
& $python migrate.py
& $python -m app.seed
& $python provision_recovery_control.py --confirm 'PROVISION KNOWN INSTALLATION careerpilot'
Set-Location ..
Set-Location web
npm.cmd ci
Set-Location ..
.\deploy\start-personal.ps1 -PythonPath $python -EnvFile $privateEnv -DatabaseReady -Port 18080 -WebPort 15173
```

启动器再次检查凭据，只绑定回环地址。普通启动不插入NORMAL、不恢复未知来源库的后台任务。已有正常安装必须在维护窗口核验来源后登记。详见[备份与隔离恢复](../deploy/BACKUP_RECOVERY.md)。

## 4. 运行时登录

打开[CareerPilot](http://127.0.0.1:15173/)，在现有WorkspaceLogin输入personal及私有文件里的API_TOKEN。不要在控制台回显秘密，不设置VITE_API_TOKEN；网页只在运行时读取标签页sessionStorage。个人模型引导和原有离线身份保持可用。

进入“考试与面经→查看题库”查看参考。真实AI对话需要主动配置模型；浏览题库和历史答卷不需要。健康检查位于[18080/healthz](http://127.0.0.1:18080/healthz)，健康不代表数据库已解除隔离。

## 5. 可选样例与下次继续

需要林舟的虚构求职记录时，在仓库根设置同一个CAREERPILOT_ENV_FILE，进入server运行 `& $python -m app.showcase`，刷新后在空间菜单选择林舟。personal记录保持独立。切换菜单在导航后生效，旧页面的请求仍使用原空间；明确退出后可以重新登录。

统一启动器使用隐藏后台进程，运行身份和日志保存在personal/runtime。不要按猜测的PID停止进程；需核对该目录的processes.json及实际命令/启动时间。独立Worker可用deploy/stop-project-worker.ps1停止。体验数据库用 `docker stop careerpilot-local-db` 停止，下一次用 `docker start careerpilot-local-db` 后以同一个EnvFile启动。保留容器和私有文件，不重新生成密码、不删卷、不影响其他个人进程。
