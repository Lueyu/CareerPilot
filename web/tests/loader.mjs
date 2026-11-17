import { readFile } from 'node:fs/promises';
import ts from 'typescript';

export async function resolve(specifier, context, nextResolve) {
  try { return await nextResolve(specifier, context); }
  catch (error) {
    if (!specifier.startsWith('.')) throw error;
    for (const suffix of ['.ts', '.tsx']) {
      try { return await nextResolve(specifier + suffix, context); } catch {}
    }
    throw error;
  }
}

export async function load(url, context, nextLoad) {
  if (url.endsWith('.json')) return { format: 'json', source: await readFile(new URL(url), 'utf8'), shortCircuit: true };
  if (!/\.tsx?$/.test(url)) return nextLoad(url, context);
  const source = await readFile(new URL(url), 'utf8');
  return { format: 'module', shortCircuit: true, source: ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
    fileName: url,
  }).outputText };
}
