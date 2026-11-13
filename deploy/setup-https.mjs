// 全自动 HTTPS：Let's Encrypt (ACME) 签发 + 阿里云 DNS-01 验证 + PutCname 证书绑定
// 证书与私钥保存在 deploy/certs/（已 gitignore），可重复运行续期。
import OSS from 'ali-oss';
import acme from 'acme-client';
import dns from 'node:dns/promises';
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const AlidnsPkg = require('@alicloud/alidns20150109');
const OpenApiCore = require('@alicloud/openapi-core');
const AlidnsClient = AlidnsPkg.default;
const { AddDomainRecordRequest, DeleteDomainRecordRequest } = AlidnsPkg;
const Config = OpenApiCore.$OpenApiUtil.Config;

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CERTS = path.join(__dirname, 'certs');

const REGION = process.env.ALI_REGION || 'oss-cn-hongkong';
const BUCKET = process.env.ALI_BUCKET || 'agent-interview-icu';
const ROOT_DOMAIN = process.env.ALI_ROOT_DOMAIN || 'agent-interview.icu';
const DOMAINS = (process.env.ALI_DOMAINS || 'agent-interview.icu,www.agent-interview.icu')
  .split(',')
  .map((s) => s.trim());
const USE_STAGING = process.env.ACME_STAGING === '1';

const log = (m) => console.log(`[https] ${m}`);

const oss = new OSS({
  region: REGION,
  accessKeyId: process.env.ALI_ACCESS_KEY_ID,
  accessKeySecret: process.env.ALI_ACCESS_KEY_SECRET,
  bucket: BUCKET,
  secure: true,
});
const dnsClient = new AlidnsClient(
  new Config({
    accessKeyId: process.env.ALI_ACCESS_KEY_ID,
    accessKeySecret: process.env.ALI_ACCESS_KEY_SECRET,
    endpoint: 'alidns.cn-hangzhou.aliyuncs.com',
  })
);

function rrOf(domain) {
  if (domain === ROOT_DOMAIN) return '@';
  return domain.slice(0, -`.${ROOT_DOMAIN}`.length);
}

// 复用或创建 ACME 账户密钥
async function getAccountKey() {
  const keyPath = path.join(__dirname, '.acme-account-key.pem');
  try {
    const existing = await readFile(keyPath);
    log('复用已有 ACME 账户密钥');
    return existing;
  } catch {
    const key = await acme.crypto.createPrivateKey(2048);
    await writeFile(keyPath, key, { mode: 0o600 });
    log('已生成新 ACME 账户密钥');
    return key;
  }
}

// 等待 TXT 在阿里公共 DNS 生效
async function waitTxtReady(fqdn, expectValue) {
  const resolver = new dns.Resolver();
  resolver.setServers(['223.5.5.5', '223.6.6.6']);
  for (let i = 0; i < 18; i += 1) {
    try {
      const records = await resolver.resolveTxt(fqdn);
      if (records.flat().join('').includes(expectValue)) return;
    } catch {
      // 未就绪
    }
    await new Promise((r) => setTimeout(r, 5000));
  }
  throw new Error(`等待 ${fqdn} TXT 生效超时`);
}

const recordIds = new Map();

async function challengeCreate(authz, challenge, keyAuthorization) {
  if (challenge.type !== 'dns-01') throw new Error(`仅支持 dns-01，收到 ${challenge.type}`);
  const domain = authz.identifier.value;
  const baseRR = rrOf(domain);
  const rr = `_acme-challenge${baseRR === '@' ? '' : '.' + baseRR}`;
  // acme-client 对 dns-01 传入的 keyAuthorization 已是最终 TXT 值（base64url(sha256(...))）
  const txtValue = keyAuthorization;
  const res = await dnsClient.addDomainRecord(
    new AddDomainRecordRequest({
      domainName: ROOT_DOMAIN,
      RR: rr,
      type: 'TXT',
      value: txtValue,
      TTL: 600,
    })
  );
  recordIds.set(domain, res.body.recordId);
  log(`添加验证 TXT ${rr}，等待 DNS 生效...`);
  await waitTxtReady(`${rr}.${ROOT_DOMAIN}`, txtValue);
  log(`${domain} 验证记录已生效`);
}

async function challengeRemove(authz) {
  const domain = authz.identifier.value;
  const recordId = recordIds.get(domain);
  if (recordId) {
    await dnsClient.deleteDomainRecord(new DeleteDomainRecordRequest({ recordId }));
    log(`已清理 ${domain} 的验证 TXT`);
  }
}

// 通过 PutCname 为指定域名绑定证书（API 直传证书与私钥）
async function bindCertificate(domain, fullchainPem, privateKeyPem) {
  const params = oss._bucketRequestParams('POST', BUCKET, { cname: '', comp: 'add' }, {});
  params.content =
    '<?xml version="1.0" encoding="UTF-8"?>\n' +
    '<BucketCnameConfiguration><Cname>' +
    `<Domain>${domain}</Domain>` +
    '<CertificateConfiguration>' +
    `<Certificate>${fullchainPem}</Certificate>` +
    `<PrivateKey>${privateKeyPem}</PrivateKey>` +
    '<Force>true</Force>' +
    '</CertificateConfiguration></Cname></BucketCnameConfiguration>';
  params.mime = 'xml';
  params.successStatuses = [200];
  return oss.request(params);
}

async function main() {
  await mkdir(CERTS, { recursive: true });

  const accountKey = await getAccountKey();
  const client = new acme.Client({
    directoryUrl: USE_STAGING
      ? acme.directory.letsencrypt.staging
      : acme.directory.letsencrypt.production,
    accountKey,
  });

  // 域名私钥 + CSR（一张证书覆盖两个域名）
  const [keyPem, csrPem] = await acme.crypto.createCsr({
    commonName: DOMAINS[0],
    altNames: DOMAINS,
    keySize: 2048,
  });

  log(`向 ${USE_STAGING ? "Let's Encrypt 测试环境" : "Let's Encrypt 生产环境"} 申请证书...`);
  const fullchain = await client.auto({
    csr: csrPem,
    challengeCreateFn: challengeCreate,
    challengeRemoveFn: challengeRemove,
    challengePriority: ['dns-01'],
    skipChallengeVerification: true,
    termsOfServiceAgreed: true,
    email: process.env.ALI_CERT_EMAIL || undefined,
  });
  const leaf = acme.crypto.splitPemChain(fullchain)[0];
  log('证书签发成功');

  await writeFile(path.join(CERTS, 'fullchain.pem'), fullchain, { mode: 0o600 });
  await writeFile(path.join(CERTS, 'private-key.pem'), keyPem, { mode: 0o600 });
  await writeFile(path.join(CERTS, 'certificate.pem'), leaf, { mode: 0o600 });

  for (const domain of DOMAINS) {
    await bindCertificate(domain, fullchain, keyPem);
    log(`已通过 PutCname 为 ${domain} 绑定证书`);
  }

  const info = acme.crypto.readCertificateInfo(fullchain);
  log(`完成。证书有效期至 ${info.notAfter.toISOString()}`);
}

main().catch((err) => {
  console.error('[https] 失败：', err.code || err.name, err.message);
  if (err.params) console.error('失败请求：', JSON.stringify(err.params, null, 2));
  process.exit(1);
});
