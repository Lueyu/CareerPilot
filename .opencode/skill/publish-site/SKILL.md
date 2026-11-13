---
name: publish-site
description: Use when the user asks to deploy, redeploy, update, publish or 上线/发布/部署 this interview-bank site to its public domain (agent-interview.icu), or asks about HTTPS certificate, OSS bucket, DNS records for it. Covers content fingerprinting, OSS upload, custom-domain binding, Let's Encrypt certificate issuance and HTTPS enforcement.
---

# 题库站点部署(publish-site)

把仓库根目录的静态站点(`index.html` + `data.js`)发布到 **https://agent-interview.icu**,并维护域名、证书与强制 HTTPS。

站点是纯静态、零依赖的单页应用:只有 `index.html`(`<script src="data.js">`)与构建产物 `data.js` 两个文件需要上线。`deploy/` 下的 Node 脚本承担全部部署工作。

## 架构

```
浏览器 → agent-interview.icu (CNAME) → OSS Bucket agent-interview-icu(中国香港,免 ICP 备案)
                                      ├─ 静态网站托管:默认首页 index.html
                                      ├─ 公共读(Block Public Access 已关闭)
                                      ├─ Bucket Policy:拒绝 acs:SecureTransport=false(即全部 HTTP)
                                      └─ 自定义域名已绑定 Let's Encrypt 证书
```

选香港节点是因为域名**没有 ICP 备案**,中国大陆节点无法绑定未备案域名。

## 前置条件

- Node.js >= 22,在 `deploy/` 下执行过一次 `npm install`
- 阿里云 AccessKey(RAM 子账号),通过环境变量传入,**绝不写入文件或提交仓库**:
  - `ALI_ACCESS_KEY_ID` / `ALI_ACCESS_KEY_SECRET`
- RAM 需要的权限:OSS 读写与 Bucket 配置、云解析(alidns)、域名证书绑定
- 域名 `agent-interview.icu` 的 NS 必须指向阿里云云解析(`dns23.hichina.com` / `dns24.hichina.com`)

## 首次完整部署(4 步)

```powershell
cd deploy
$env:ALI_ACCESS_KEY_ID='...'; $env:ALI_ACCESS_KEY_SECRET='...'

npm run build        # 生成 dist:index.html + data.js(data.js 自动加内容指纹)
npm run deploy:oss   # 建/配 Bucket、上传、域名所有权验证、绑定、写 CNAME
npm run deploy:https # Let's Encrypt 签发证书并 PutCname 绑定
npm run enforce:https # 写入拒绝 HTTP 的 Bucket Policy
```

`deploy:oss` 与 `deploy:https` 都可重复执行:

- Bucket 已存在→沿用;域名已绑定→`NoNeedCreateCnameToken` 直接跳过验证
- 证书到期前重跑 `deploy:https` 即可续期(`Force=true` 覆盖旧证书)

## 日常更新内容(最常见)

题库改动后(`python build.py` 重新生成 `data.js`)只需两步:

```powershell
cd deploy
$env:ALI_ACCESS_KEY_ID='...'; $env:ALI_ACCESS_KEY_SECRET='...'
npm run build        # 重新生成 dist,data.js 指纹随之变化
npm run deploy:oss   # 覆盖上传;已绑定的域名与 CNAME 会幂等跳过
```

证书、域名绑定、强制 HTTPS 策略都长期有效,**不需要重做**。

### 为什么必须带内容指纹

`prepare.mjs` 会把 `data.js` 的 sha256 前 10 位写进 `index.html` 的引用(`data.js?v=xxxxxxxxxx`)。这样题库更新后 URL 变化,浏览器不会继续用旧缓存;上传时 `index.html` 用 `max-age=0, must-revalidate`(每次都校验,保证拿到最新指纹),带指纹的 `data.js` 用 `max-age=31536000, immutable`(可长期强缓存)。

手动改 `index.html` 的 `data.js` 引用、或去掉 `?v=` 会导致线上出现「页面是新的、数据是旧的」。

## 验证

```powershell
cd deploy
node verify.mjs
```

检查项:两个域名证书是否受信任、页面与 `data.js` 是否 200、`data.js` 的 MIME/缓存头、未知路径是否 404、HTTP 是否 403。结果写入 `deploy/verify-https.json`,并在终端逐项打印 PASS/FAIL。

## 排障(本方案踩过的坑)

| 现象 | 原因与处理 |
|---|---|
| 脚本第一步就 403 | Bucket 已强制 HTTPS,OSS 客户端必须显式 `secure: true`(所有脚本已内置) |
| 绑定域名报 `409 BucketAlreadyExists` | 用了旧的 `PUT /?cname`;必须用 `POST /?cname&comp=add` + `BucketCnameConfiguration` |
| `createToken` 返回 `NoNeedCreateCnameToken` | 域名已绑定,属正常,跳过验证即可 |
| `putBucketACL` 被拒 | 需先关闭 Bucket 级「阻止公共访问」,否则公共读 ACL 不生效 |
| HTTPS 刚绑定时握手失败/返回阿里云默认证书 | 证书下发到边缘节点有延迟,查 `GET /?cname` 确认证书状态为 Enabled,再等 1~5 分钟 |
| 本机打不开但公网可用 | 上游 DNS 对 apex 有过「无记录」负缓存;`Clear-DnsClientCache`,或用 `Resolve-DnsName -Server 8.8.8.8` 对照;浏览器可 `Ctrl+F5` |
| 阿里云免费 DV 证书想 API 签发 | 需先购买证书资源包,CAS 无法直接签;本方案改用 Let's Encrypt + DNS-01 |

## 安全红线

- **绝不提交** `deploy/certs/`、`deploy/.acme-account-key.pem`(私钥)、`deploy/dist/`、`deploy/node_modules/`、`deploy/verify-https.json`(均已 gitignore)
- AccessKey 只用环境变量传递;不要写进脚本、README 或提交历史
- 不要 `git add -A`;提交前 `git status --short` 确认没有个人数据(`notes.json`)与密钥
