# CareerPilot 公开种子版实施计划

目标是在两个互相独立的新账号中完成岗位选择、私有导入、学习、面试和再次登录。注册版与个人版共用网页入口及工作台路由，构建时装配账号对应的页面。注册版的 FastAPI 根据登录身份访问工作空间，复用题目、材料解析、笔记和学习计划服务。上线前验证同一构建产物。

| 顺序 | 改动位置 | 交付与检查 |
| --- | --- | --- |
| 1 | server/app/api/public_auth.py、web/src/seed/Account.tsx | 事务创建本人空间，服务端 Cookie、来源校验、限速、找回撤销、删除。测试两个独立账号的恢复和隔离。 |
| 2 | server/app/services/public_model.py | 真实模型调用前按价格和上限预留，按用量结算，断流保持未知。隔离库验证并发预算。 |
| 3 | server/app/api/public_library.py、web/src/seed/Library.tsx | 按岗位保存方向和私有题，复用文档提取与审核，支持三个格式、原答案和选择确认。 |
| 4 | server/app/api/public_coach.py、web/src/seed/Coach.tsx | 学习、出题候选与收藏、独立作答和追问，复用已确认的经历。验证收藏幂等、保存状态、模型错误和恢复。 |
| 5 | server/app/api/public_memory.py、server/app/public_main.py、web/src/seed/RegisteredApp.tsx | 连接各模块，实现用户确认的项目记忆、私有数据导出、反馈、隐私和原工作台路由接入。 |
| 6 | deploy/public、web/vite.seed.config.ts | 独立数据库和容器、静态内容白名单及加密备份恢复，核验产物不含个人静态题库。 |
| 7 | server/tests、隔离验收实例 | 在最终代码上执行隔离测试、前端构建和双账号浏览器路径。记录真实模型与受控测试分别达成的行为。 |
| 8 | 阿里云交付 | 以有效服务器、域名接入、模型配置为条件验证真实公网和大陆双网表现。采购、变更 DNS 或发布到现有站点以前确认具体目标和开支。 |

接口共用 /api/v1。公开模型调用统一经过限额服务。用户身份从 Cookie 解析，不接受浏览器传来的空间身份。目录隔离所有写入，按依赖整合后运行隔离集成检查。
