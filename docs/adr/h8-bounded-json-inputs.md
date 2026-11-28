# H8：领域输入契约、实际接收字节与全集分页

状态：本地实现，待父代理独立审核。基线为 H2 c4d9ad3ff7d9318b675afa31d2b677d535f6ed00。

## 背景

五个请求中的嵌套字典绕过了外层 Pydantic 字段的语义和资源约束。遥测是匿名入口；只限制事件数、只相信 Content-Length 或在反序列化后遍历，不能限制真实收到的请求体。bank 的单页上限 5000 还有一个依赖全集的 Questions.tsx 调用方。

## 决定

用现有 Pydantic 严格模型描述草稿编辑、非 PROJECT 原答与辅助声明。禁止未知领域字段和数字/字符串布尔；JSON 对象中的十进制正整数 ID 键显式解析为整数。缺失/null/空数组保持原业务差异。导出到既有服务时使用 exclude_unset=True，不重写原答、别名或旧请求指纹。项目来源、服务器快照、辅助使用观察与 PRACTICE/正式能力证据分离仍由现有服务掌握。

评分 rubric、遥测 props 与发布规则 value 保留合法的自由 JSON 对象。用 Pydantic JsonValue 验证类型，并以前置迭代遍历限制深度 8、节点 1024、单对象字段/单数组元素 128、键长 128、字符串长 8192，以及有限浮点数。规则 key 长度 64 与数据库列一致，不发明规则白名单。

精确路由 ASGI 层在每个 receive 事件后计数，在超限块进入应用缓冲前返回 413。telemetry 为 128 KiB，学习提交和规则为 512 KiB，粘贴导入和审批为 2 MiB。正常包缓冲最多到上限后重放，不依赖任何长度头；不继续消费超限后的尾块。按 JSON 编码识别 UTF-8/16/32，字符串外括号深度超过 32 时在 JSON 解码前返回 422。合法多块、断连和取消保留协议语义。此层不接管 multipart 上传、GET/SSE 或其他接口。

入站顺序保留 CORS → RecoveryGuard → ApiRateLimit → Language → JsonInputBoundary → FastAPI。因此早期拒绝继续携带现有 X-Request-ID/CORS/语言头；受保护接口先完成既有鉴权与配额准入，安全计数可以写入，但题库、学习、评分、规则与遥测业务不得写入。范围内的 Pydantic 422 保留 type/loc/msg，不回显整份输入，避免 NaN/Infinity 令错误响应自身编码失败。范围外继续使用 FastAPI 默认错误处理，此决定不替代 F7。

bank 单页最多 200，兼容默认 60 和 Bank.tsx 的 20。全集调用方按 total 顺序翻页，全部成功才发布库存，支持 AbortSignal、旧查询失效、失败后重试；空页、重复 ID、总数变化均显式失败。405 题覆盖三个请求；本项不增加页面或重做视觉设计。

## 备选与后果

备选方案是在各路由定制 Request/APIRoute 并引入流式 JSON 解析器。它可以减少上限内缓冲，但增加解析依赖、错误映射以及默认 request.json 绕过风险。这些接口是有界的非流式 JSON，本次选择复用 Pydantic 与 ASGI 成熟接口。只检查 Content-Length 和事后 walker 均不满足真实字节边界。

每个请求仍有上限内缓冲及一次深度扫描，不承诺网络层零分配或并发内存总上限。HTTP 服务器交付的单个 chunk 已由服务器分配，本层拒绝将超限 chunk 加入应用缓冲。全集在浏览器中仍随题库大小增长；分页不是数据库快照，并发修改可能触发显式重试，同总数替换不能被此 offset 协议完整检测。真实编译应用浏览器跨页核验由父代理在冻结后执行。

验证入口为 tests/test_bounded_inputs.py、tests/test_bounded_inputs_http.py、web/tests/bank-inventory.test.mjs。实际命令、退出码、红绿日志与未覆盖边界保存在 .agent-runs/20261128-wave1/h8/。
