# 第一次运行 CareerPilot

准备Git、Python3.12和带Compose v2的Docker，Windows/macOS可使用Docker Desktop。以下python指专用Python环境的解释器。本流程只用于全新PG卷；已有个人.env、PG角色、密码和数据应保留，旧弱凭据由操作者在维护窗口迁移。

## 生成一次，重复使用

在新目录下载代码并安装初始化器依赖：

```shell
git clone https://github.com/Lueyu/CareerPilot.git
cd CareerPilot
python -m pip install pydantic-settings sqlalchemy cryptography
python server/personal_credentials.py init --output personal/runtime/personal.env
```

Windows虚拟环境使用Scripts/python.exe，Unix使用bin/python。初始化器不连接数据库，生成独立随机API令牌、PG密码和G5主密钥。文件只在缺失时创建，重复执行只校验，不覆盖、不轮换。Windows在写秘密前限制并回读新文件/目录DACL，权限无法确认则停止。.env.example是空变量清单，不能复制后直接启动。不要提交、分享私有配置或把它放进前端构建。

## 初始化已知新库并显式登记

每条成功后继续下一条。首次构建会下载镜像和依赖。Compose先完成凭据preflight，成功后才允许PostgreSQL初始化：

```shell
docker compose --env-file personal/runtime/personal.env up -d --build postgres
docker compose --env-file personal/runtime/personal.env run --rm --no-deps server sh -c 'python personal_credentials.py check && python bootstrap.py && python migrate.py && python -m app.seed'
docker compose --env-file personal/runtime/personal.env run --rm --no-deps server python provision_recovery_control.py --confirm 'PROVISION KNOWN INSTALLATION careerpilot'
docker compose --env-file personal/runtime/personal.env up -d --build --wait
```

登记只用于刚创建且核实来源的新库，或维护窗口核实的正常安装。恢复数据库禁止登记。普通启动不插入NORMAL，不恢复未知来源库的后台任务；详见[备份与隔离恢复](../deploy/BACKUP_RECOVERY.md)。新随机密码不能用于旧PG卷，环境变量也不会自动改变旧角色密码。

## 运行时登录与练习

打开[本机工作空间](http://127.0.0.1:5173/)，在现有WorkspaceLogin输入personal及私有文件里的API_TOKEN。令牌仅在运行时保存于当前标签页sessionStorage，不设置VITE_API_TOKEN。退出后重新登录，空间切换在导航后生效。顶部语言菜单可选简体中文或English。

进入“考试与面经→查看题库”，选择一道题查看参考，也可导入自己的面经并审核。首次零证据、零任务和UNKNOWN是正常起点。浏览题库和历史答卷不需要模型密钥。

要查看虚构的“林舟的求职笔记”，执行后刷新并在空间菜单切换：

```shell
docker compose --env-file personal/runtime/personal.env exec server python -m app.showcase
```

personal记录保持独立。

## 模型、停止和排查

AI对话需要操作者主动配置模型。在受保护的personal/runtime/personal.env填写LLM_PROVIDER、LLM_BASE_URL、LLM_MODEL和LLM_API_KEY，再运行同一env-file的up -d --wait。模型调用可能收费，并把练习内容发送给该服务；浏览题库不需要这一步。

- 停止：docker compose --env-file personal/runtime/personal.env stop。继续：同一个文件运行up -d --wait。不重新生成凭据、不删卷。
- 端口冲突：调整私有文件的PG_HOST_PORT、API_HOST_PORT或WEB_HOST_PORT，保留127.0.0.1前缀，例如WEB_HOST_PORT=127.0.0.1:15173。
- 状态检查：同一env-file运行ps -a。preflight应正常退出0，其余四个服务运行。健康探针可用不等于数据库已解除隔离。
- 排障：同一env-file运行logs --tail=60 preflight server web。不要公开环境快照或含密码的完整Compose配置。下载失败先检查Docker网络/代理。

[Windows分别启动前后端](getting-started-windows.md) · [个人使用与升级](../QUICK_START_PERSONAL.md) · [备份与恢复](../deploy/BACKUP_RECOVERY.md)
