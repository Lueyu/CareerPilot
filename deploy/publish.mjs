#!/usr/bin/env node
// deploy/publish.mjs —— 全自动发布闸门链(super-agent v2 规格 §2④)
// 用法:
//   node publish.mjs               # 全链:build→prepare→vault→快照→泄漏扫描→commit→push→OSS→verify,verify 失败自动回滚
//   node publish.mjs --dry-run     # 演练:跑到 push 前停下,打印暂存清单
//   node publish.mjs --vault-only  # 每日 vault 日更:prepare→vault→OSS→verify(不动 git)
import { spawnSync } from 'node:child_process';
import { cpSync, rmSync, existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const DIST = path.join(__dirname, 'dist');
const PREV = path.join(__dirname, 'dist.prev');
const PAYLOAD = path.join(ROOT, 'personal', 'vault-payload.json');

const WHITELIST = ['content/', 'docs/', '.opencode/', 'icons/', 'index.html', 'data.js',
  'build.py', 'manifest.webmanifest', 'sw.js', 'README.md',
  'deploy/*.mjs', 'deploy/package.json', 'deploy/package-lock.json'];

export const PATH_DENY = /(^|\/)(personal\/|notes\.json$|\.backup-|\.vault-key|drafts\/)/;
export const KEY_DENY = /(BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY|LTAI[0-9A-Za-z]{12,}|accessKeySecret|ALI_ACCESS_KEY_SECRET)/;
export function findLeaks(paths, diffText) {
  const badPaths = (paths || []).filter((p) => PATH_DENY.test(p));
  if (badPaths.length) return { paths: badPaths };
  const m = diffText && diffText.match(KEY_DENY);
  return m ? { pattern: m[0] } : null;
}

function run(cmd, args, cwd) {
  const r = spawnSync(cmd, args, { stdio: 'inherit', cwd: cwd || __dirname });
  if (r.status !== 0) { console.error('[publish] 步骤失败:', cmd, args.join(' ')); process.exit(1); }
}
function cap(cmd, args, cwd) {
  return spawnSync(cmd, args, { cwd: cwd || __dirname, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 }).stdout || '';
}
function snapshot() {
  rmSync(PREV, { recursive: true, force: true });
  if (existsSync(DIST)) cpSync(DIST, PREV, { recursive: true });
}
function stageAndScan(dry) {
  run('git', ['add', ...WHITELIST], ROOT);
  const changed = cap('git', ['diff', '--cached', '--name-only'], ROOT).trim().split('\n').filter(Boolean);
  if (!changed.length) { console.log('[publish] 仓库无变更,跳过 commit/push'); return false; }
  const diff = cap('git', ['diff', '--cached', '-U0'], ROOT);
  const leak = findLeaks(changed, diff);
  if (leak) {
    run('git', ['reset'], ROOT);
    console.error('[publish] 泄漏扫描命中,已中止:', JSON.stringify(leak));
    process.exit(1);
  }
  console.log('[publish] 泄漏扫描通过,' + changed.length + ' 个文件变更');
  if (dry) {
    changed.forEach((c) => console.log('  将提交: ' + c));
    console.log('[publish] DRY-RUN 到此为止(未 commit/push/部署)');
    return false;
  }
  const msg = 'chore(auto): ' + new Date().toISOString().slice(0, 10) + ' 自动发布';
  run('git', ['commit', '-m', msg], ROOT);
  return true;
}
function rollbackAndExit() {
  console.error('[publish] verify 失败 → 回滚上一版');
  if (existsSync(PREV)) {
    rmSync(DIST, { recursive: true, force: true });
    cpSync(PREV, DIST, { recursive: true });
    run('node', ['deploy-oss.mjs']);
  } else {
    console.error('[publish] 无 dist.prev 快照,跳过 OSS 回滚');
  }
  const last = cap('git', ['log', '-1', '--pretty=%s'], ROOT);
  if (last.includes('自动发布')) {
    run('git', ['revert', '--no-edit', 'HEAD'], ROOT);
    run('git', ['push', 'origin', 'master'], ROOT);
  }
  process.exit(2);
}
function deploy() {
  if (!process.env.ALI_ACCESS_KEY_ID || !process.env.ALI_ACCESS_KEY_SECRET) {
    console.error('[publish] 缺 ALI_ACCESS_KEY_ID/ALI_ACCESS_KEY_SECRET 环境变量,请先在持久 shell 导出后重跑');
    process.exit(1);
  }
  run('node', ['deploy-oss.mjs']);
  const v = spawnSync('node', ['verify.mjs'], { stdio: 'inherit' });
  if (v.status !== 0) rollbackAndExit();
  console.log('[publish] 发布完成且 verify 通过 ✔');
}
function main() {
  const mode = process.argv[2] || '';
  const dry = mode === '--dry-run';
  const vaultOnly = mode === '--vault-only';
  if (!vaultOnly && !dry) run('python', ['build.py'], ROOT);
  run('node', ['prepare.mjs']);
  if (!existsSync(PAYLOAD)) console.warn('[publish] personal/vault-payload.json 缺失,vault.js 未更新');
  else run('node', ['vault.mjs', 'encrypt', PAYLOAD]);
  snapshot();
  if (vaultOnly) { deploy(); return; }
  const committed = stageAndScan(dry);
  if (dry) return;
  if (committed) run('git', ['push', 'origin', 'master'], ROOT);
  deploy();
}
if (process.argv[1] === fileURLToPath(import.meta.url)) main();
