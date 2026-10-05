import js from '@eslint/js';
import vue from 'eslint-plugin-vue';
import prettierConfig from 'eslint-config-prettier';
import globals from 'globals';
import tseslint from 'typescript-eslint';
import vueParser from 'vue-eslint-parser';

// Third-party libraries loaded via <script> tags in the Flask/HTML templates.
const vendorGlobals = {
  L: 'readonly',
  THREE: 'readonly',
  JSZip: 'readonly',
  turf: 'readonly',
  Chart: 'readonly',
};

export default [
  {
    ignores: [
      'node_modules/**',
      '**/node_modules/**',
      'dist/**',
      'build/**',
      '.git/**',
      '**/*.min.js',
      'vendor/**',
      '**/vendor/**',
      'app/client/static/dist/**',
      '.venv/**',
      'htmlcov/**',
      'docs_site/**',
      'sphinx_site/**',
      'output/**',
      'cache/**',
      'runs/**',
    ],
  },
  js.configs.recommended,
  ...vue.configs['flat/essential'],
  {
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        ...globals.browser,
        ...vendorGlobals,
      },
    },
  },
  {
    files: ['**/*.vue'],
    languageOptions: {
      parser: vueParser,
      parserOptions: {
        parser: tseslint.parser,
        ecmaVersion: 'latest',
        sourceType: 'module',
        extraFileExtensions: ['.vue'],
      },
    },
  },
  {
    files: ['**/*.ts'],
    languageOptions: { parser: tseslint.parser },
  },
  {
    files: ['**/workers/**/*.js'],
    languageOptions: { globals: { ...globals.worker } },
  },
  {
    files: ['tests/**/*.{js,ts}', '*.config.js', 'scripts/**/*.js'],
    languageOptions: { globals: { ...globals.node } },
  },
  {
    files: ['**/*.{js,ts,vue}'],
    rules: {
      'vue/multi-word-component-names': 'warn',
      'no-unused-vars': ['error', { argsIgnorePattern: '^_', caughtErrors: 'none' }],
      'no-console': process.env.NODE_ENV === 'production' ? 'warn' : 'off',
    },
  },
  {
    // Type-only identifiers (interfaces, type params) confuse the core rules.
    files: ['**/*.{ts,vue}'],
    plugins: { '@typescript-eslint': tseslint.plugin },
    rules: {
      'no-undef': 'off',
      'no-unused-vars': 'off',
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', caughtErrors: 'none' },
      ],
    },
  },
  // Must be last: turns off stylistic rules that conflict with Prettier.
  { rules: prettierConfig.rules },
];
