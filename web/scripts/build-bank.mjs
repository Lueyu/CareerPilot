// 从仓库根目录 data.js（window.QB）生成前端题库数据 web/src/data/bank.json
// 用法: node scripts/build-bank.mjs
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..", "..");
const dataPath = path.join(root, "data.js");
const outPath = path.join(__dirname, "..", "src", "data", "bank.json");

const raw = fs.readFileSync(dataPath, "utf8");
const jsonText = raw.replace(/^window\.QB\s*=\s*/, "").replace(/;\s*$/, "");
const qb = JSON.parse(jsonText);

const questions = (qb.questions || []).map((q) => ({
  id: q.id,
  topic: q.topic,
  title: q.title,
  difficulty: q.difficulty,
  tags: q.tags || [],
  company: q.company || "",
  source: q.source || "",
  kw: q.kw || "",
  mcq: q.mcq || null,
  html: q.html || "",
}));

const out = {
  version: qb.version ?? 1,
  builtAt: qb.builtAt ?? "",
  topics: qb.topics || [],
  glossary: qb.glossary || [],
  questions,
};

fs.mkdirSync(path.dirname(outPath), { recursive: true });
fs.writeFileSync(outPath, JSON.stringify(out), "utf8");
console.log(`bank.json 生成完成: ${questions.length} 题 -> ${outPath}`);
