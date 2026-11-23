import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';

export default tseslint.config(
  // 范围：web/src 的 TS/TSX。tests/*.mjs 与 scripts/*.mjs 是 Node 上下文（不同 globals），暂不纳入。
  { ignores: ['dist/', 'node_modules/', 'scripts/', 'tests/'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['src/**/*.{ts,tsx}'],
    plugins: { 'react-hooks': reactHooks },
    rules: {
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      '@typescript-eslint/no-explicit-any': 'off',
      // JSX 文案里的全角空格（U+3000）是有意排版，不按不规则空白报错
      'no-irregular-whitespace': 'off',
    },
  },
);
