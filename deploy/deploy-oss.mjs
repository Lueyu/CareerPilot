// 把 dist 上传到阿里云 OSS（中国香港节点，免 ICP 备案），并完成域名所有权验证、CNAME 绑定与云解析。
// 凭证通过环境变量传入，不写入任何文件：
//   ALI_ACCESS_KEY_ID / ALI_ACCESS_KEY_SECRET
// 可选覆盖：
//   ALI_REGION（默认 oss-cn-hongkong）、ALI_BUCKET（默认 agent-interview-icu）、
//   ALI_ROOT_DOMAIN（默认 agent-interview.icu）、
//   ALI_DOMAINS（默认 agent-interview.icu,www.agent-interview.icu，逗号分隔）
import OSS from 'ali-oss';
import { readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import dns from 'node:dns/promises';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const AlidnsPkg = require('@alicloud/alidns20150109');
const OpenApiCore = require('@alicloud/openapi-core');
const AlidnsClient = AlidnsPkg.default;
const {
  AddDomainRecordRequest,
  DeleteDomainRecordRequest,
  DescribeDomainRecordsRequest,
} = AlidnsPkg;
const Config = OpenApiCore.$OpenApiUtil.Config;

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DIST = path.join(__dirname, 'dist');

const REGION = process.env.ALI_REGION || 'oss-cn-hongkong';
const BUCKET = process.env.ALI_BUCKET || 'agent-interview-icu';
const ROOT_DOMAIN = process.env.ALI_ROOT_DOMAIN || 'agent-interview.icu';
const DOMAINS = (process.env.ALI_DOMAINS || 'agent-interview.icu,www.agent-interview.icu')
  .split(',')
  .map((d) => d.trim())
  .filter(Boolean);
const CNAME_TARGET = `${BUCKET}.${REGION}.aliyuncs.com`;

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.ico': 'image/x-icon',
};

const log = (step, msg) => console.log(`[${step}] ${msg}`);
const fail = (msg) => {
  console.error(`[ERROR] ${msg}`);
  process.exit(1);
};

const accessKeyId = process.env.ALI_ACCESS_KEY_ID;
const accessKeySecret = process.env.ALI_ACCESS_KEY_SECRET;
if (!accessKeyId || !accessKeySecret) {
  fail('缺少凭证，请先设置环境变量 ALI_ACCESS_KEY_ID 和 ALI_ACCESS_KEY_SECRET。');
}

// 递归收集 dist 下全部文件，返回 [绝对路径, OSS 对象名]
async function collectFiles(dir, base = dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await collectFiles(full, base)));
    } else {
      files.push([full, path.relative(base, full).split(path.sep).join('/')]);
    }
  }
  return files;
}

// 完整域名 -> 主机记录 RR，主域名用 @
function toRR(domain) {
  if (domain === ROOT_DOMAIN) return '@';
  if (domain.endsWith(`.${ROOT_DOMAIN}`)) {
    return domain.slice(0, -`.${ROOT_DOMAIN}`.length);
  }
  fail(`域名 ${domain} 不属于 ${ROOT_DOMAIN}，无法自动配置。`);
}

// 关闭 Bucket 级“阻止公共访问”，否则公共读 ACL 会被拒绝
async function disablePublicAccessBlock(client) {
  const params = client._bucketRequestParams('PUT', BUCKET, 'publicAccessBlock', {});
  params.content =
    '<?xml version="1.0" encoding="UTF-8"?>\n' +
    '<PublicAccessBlockConfiguration><BlockPublicAccess>false</BlockPublicAccess></PublicAccessBlockConfiguration>';
  params.mime = 'xml';
  params.successStatuses = [200];
  return client.request(params);
}

// 新版 PutCname：POST /?cname&comp=add，XML 根节点 BucketCnameConfiguration
async function bindCname(client, domain) {
  const params = client._bucketRequestParams('POST', BUCKET, { cname: '', comp: 'add' }, {});
  params.content =
    '<?xml version="1.0" encoding="UTF-8"?>\n' +
    `<BucketCnameConfiguration><Cname><Domain>${domain}</Domain></Cname></BucketCnameConfiguration>`;
  params.mime = 'xml';
  params.successStatuses = [200];
  return client.request(params);
}

// CreateCnameToken：POST /?cname&comp=token，返回所有权验证 Token
async function createToken(client, domain) {
  const params = client._bucketRequestParams('POST', BUCKET, { cname: '', comp: 'token' }, {});
  params.content =
    '<?xml version="1.0" encoding="UTF-8"?>\n' +
    `<BucketCnameConfiguration><Cname><Domain>${domain}</Domain></Cname></BucketCnameConfiguration>`;
  params.mime = 'xml';
  params.successStatuses = [200];
  const res = await client.request(params);
  const xml = Buffer.from(res.data).toString('utf8');
  const m = xml.match(/<Token><!\[CDATA\[(.+?)\]\]><\/Token>|<Token>(.+?)<\/Token>/);
  return m ? m[1] || m[2] : null;
}

// 确保 _dnsauth[.子域] TXT 记录值为最新 token；旧值会先删除再更新
async function ensureTxt(dnsClient, domain, token) {
  const baseRR = toRR(domain);
  const rr = `_dnsauth${baseRR === '@' ? '' : '.' + baseRR}`;
  const existing = await dnsClient.describeDomainRecords(
    new DescribeDomainRecordsRequest({
      domainName: ROOT_DOMAIN,
      RRKeyWord: rr,
      searchMode: 'EXACT',
    })
  );
  const records = (existing.body.domainRecords?.record || []).filter((r) => r.RR === rr && r.type === 'TXT');
  const matched = records.find((r) => r.value === token);
  if (matched) {
    log('5/7', `  TXT ${rr} 已为最新，跳过`);
  } else {
    for (const rec of records) {
      await dnsClient.deleteDomainRecord(new DeleteDomainRecordRequest({ recordId: rec.recordId }));
      log('5/7', `  删除旧 TXT ${rr}（${rec.value.slice(0, 12)}...）`);
    }
    await dnsClient.addDomainRecord(
      new AddDomainRecordRequest({
        domainName: ROOT_DOMAIN,
        RR: rr,
        type: 'TXT',
        value: token,
        TTL: 600,
      })
    );
    log('5/7', `  已添加 TXT ${rr}`);
  }
  return rr;
}

async function waitTxt(rr, token) {
  const fqdn = `${rr}.${ROOT_DOMAIN}`;
  const resolver = new dns.Resolver();
  resolver.setServers(['223.5.5.5', '223.6.6.6']);
  for (let i = 0; i < 18; i += 1) {
    try {
      const records = await resolver.resolveTxt(fqdn);
      if (records.flat().join('').includes(token)) {
        log('5/7', `  TXT 已生效（约 ${i * 10} 秒）`);
        return;
      }
    } catch {
      // 未就绪，继续等待
    }
    await new Promise((r) => setTimeout(r, 10000));
  }
  fail(`等待 TXT ${fqdn} 生效超时`);
}

// 确保 CNAME 指向本 Bucket；存在冲突的其他记录则报错交人工确认
async function ensureCname(dnsClient, domain) {
  const rr = toRR(domain);
  const existing = await dnsClient.describeDomainRecords(
    new DescribeDomainRecordsRequest({
      domainName: ROOT_DOMAIN,
      RRKeyWord: rr,
      searchMode: 'EXACT',
    })
  );
  const sameRR = (existing.body.domainRecords?.record || []).filter((r) => r.RR === rr);
  const ok = sameRR.find((r) => r.type === 'CNAME' && r.value === CNAME_TARGET);
  if (ok) {
    log('6/7', `  CNAME ${rr} 已存在且正确，跳过`);
    return;
  }
  if (sameRR.length > 0) {
    const detail = sameRR.map((r) => `${r.type} -> ${r.value}`).join('; ');
    fail(`主机记录 ${rr} 存在冲突记录（${detail}），请在云解析控制台确认后重跑。`);
  }
  await dnsClient.addDomainRecord(
    new AddDomainRecordRequest({
      domainName: ROOT_DOMAIN,
      RR: rr,
      type: 'CNAME',
      value: CNAME_TARGET,
      TTL: 600,
    })
  );
  log('6/7', `  已添加 CNAME：${rr} -> ${CNAME_TARGET}`);
}

async function main() {
  const client = new OSS({
    region: REGION,
    accessKeyId,
    accessKeySecret,
    bucket: BUCKET,
    secure: true, // 强制 HTTPS 后 Bucket Policy 拒绝所有 HTTP 请求，必须显式启用
  });
  const dnsClient = new AlidnsClient(
    new Config({
      accessKeyId,
      accessKeySecret,
      endpoint: 'alidns.cn-hangzhou.aliyuncs.com',
    })
  );

  // 1. 确认 Bucket 存在，不存在则创建
  log('1/7', `检查 Bucket ${BUCKET}（${REGION}）...`);
  try {
    await client.getBucketInfo(BUCKET);
    log('1/7', 'Bucket 已存在，沿用。');
  } catch (err) {
    if (err.code !== 'NoSuchBucket') throw err;
    log('1/7', 'Bucket 不存在，开始创建（标准存储 + 公共读）...');
    try {
      await client.putBucket(BUCKET, { acl: 'public-read' });
    } catch (createErr) {
      if (createErr.code === 'BucketAlreadyExists') {
        fail(`Bucket 名称 ${BUCKET} 已被其他账号占用，请设置 ALI_BUCKET 换一个名称后重试。`);
      }
      throw createErr;
    }
  }

  // 2. 关闭阻止公共访问并设置公共读
  log('2/7', '关闭 Bucket 级阻止公共访问...');
  await disablePublicAccessBlock(client);
  log('2/7', '设置 Bucket 权限为公共读...');
  await client.putBucketACL(BUCKET, 'public-read');

  // 3. 静态网站托管：默认首页 index.html，错误页回退 index.html
  log('3/7', '配置静态网站托管（默认首页 index.html）...');
  await client.putBucketWebsite(BUCKET, { index: 'index.html', error: 'index.html' });

  // 4. 上传全部静态文件
  const files = await collectFiles(DIST);
  log('4/7', `上传 ${files.length} 个文件...`);
  for (const [absPath, objectName] of files) {
    const mime = MIME[path.extname(absPath).toLowerCase()] || 'application/octet-stream';
    // HTML 每次使用前校验，保证总能拿到最新的 data.js 指纹；带指纹的 data.js 可长期强缓存
    const cacheControl =
      objectName.endsWith('.html')
        ? 'public, max-age=0, must-revalidate'
        : 'public, max-age=31536000, immutable';
    await client.put(objectName, absPath, { mime, headers: { 'Cache-Control': cacheControl } });
    log('4/7', `  uploaded ${objectName} (${mime})`);
  }

  // 5. 域名所有权验证并绑定
  log('5/7', `验证并绑定域名：${DOMAINS.join('、')} ...`);
  for (const domain of DOMAINS) {
    let token;
    try {
      token = await createToken(client, domain);
    } catch (err) {
      // 域名已绑定在本 Bucket 时，OSS 不再签发 token，直接跳过即可
      if (err.code === 'NoNeedCreateCnameToken') {
        log('5/7', `  ${domain} 已绑定，跳过域名验证`);
        continue;
      }
      throw err;
    }
    if (!token) fail(`${domain} 未获取到 Token`);
    log('5/7', `  ${domain} Token: ${token}`);
    const rr = await ensureTxt(dnsClient, domain, token);
    await waitTxt(rr, token);
    await bindCname(client, domain);
    log('5/7', `  ${domain} 绑定成功`);
  }

  // 6. 配置 CNAME 解析
  log('6/7', '配置云解析 CNAME ...');
  for (const domain of DOMAINS) {
    await ensureCname(dnsClient, domain);
  }

  // 7. 汇总
  console.log('');
  log('7/7', 'OSS + DNS 自动化配置全部完成。');
  console.log('----------------------------------------');
  console.log('Bucket 外网域名：', CNAME_TARGET);
  console.log('静态网站访问：', `http://${CNAME_TARGET.replace(`.${REGION}.`, `.${REGION}-website.`)}`);
  console.log('自定义域名：', DOMAINS.map((d) => `https://${d}`).join('、'));
  console.log('下一步：npm run deploy:https 申请证书并绑定，再 npm run enforce:https 强制 HTTPS。');
}

main().catch((err) => {
  console.error('[ERROR] 部署失败：', err.code || err.name, err.message);
  if (err.params) console.error('失败请求：', JSON.stringify(err.params, null, 2));
  process.exit(1);
});
