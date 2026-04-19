import js from '@eslint/js';
import vue from 'eslint-plugin-vue';
import prettier from '@vue/eslint-config-prettier';

export default [
  js.configs.recommended,
  ...vue.configs['flat/essential'],
  prettier,
  {
    files: ['**/*.{js,vue}'],
    rules: {
      'vue/multi-word-component-names': 'warn',
      'no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
      'no-console': process.env.NODE_ENV === 'production' ? 'warn' : 'off',
    },
  },
  {
    ignores: [
      'node_modules/**',
      'dist/**',
      'build/**',
      '.git/**',
      '**/*.min.js',
      'vendor/**',
    ],
  },
];
