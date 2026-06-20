import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      // ── Code quality ──

      // Cyclomatic complexity — flag functions with too many branches
      'complexity': ['warn', { max: 15 }],

      // Max lines per file — large file detection
      'max-lines': ['warn', { max: 500, skipBlankLines: true, skipComments: true }],

      // ── Agent-enforced rules (see AGENTS.md) ──

      // Ban raw useEffect — use useMountEffect, TanStack Query, or event handlers
      'no-restricted-imports': ['error', {
        paths: [{
          name: 'react',
          importNames: ['useEffect'],
          message: 'useEffect is banned. Use useMountEffect() for mount-only effects, TanStack Query for data fetching, or compute inline for derived state. See AGENTS.md §1.',
        }],
      }],

      // Ban lucide-react — use @phosphor-icons/react weight="thin"
      'no-restricted-syntax': ['error',
        {
          selector: 'ImportDeclaration[source.value="lucide-react"]',
          message: 'lucide-react is banned. Use @phosphor-icons/react with weight="thin". See AGENTS.md §1.',
        },
      ],
    },
  },
])
