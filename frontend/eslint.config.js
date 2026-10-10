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
      // Most react-hooks rules run on the React Compiler's analysis. When the
      // compiler gives up on a component or hook, those rules report nothing
      // for it, and the recommended set leaves most of the diagnostics that
      // say so switched off. These four are such diagnostics:
      //   todo             a construct the compiler does not handle yet
      //                    (`try ... finally`, `??=`, a ternary inside a `try`)
      //   invariant        the compiler failed one of its own checks (an
      //                    optional chain inside a `try` in a component body)
      //   syntax           code it rejects (a reassigned `const`)
      //   rule-suppression a disable comment for rules-of-hooks or
      //                    exhaustive-deps, which makes it skip the function
      // src/lintGate.test.ts plants each of those examples.
      'react-hooks/todo': 'error',
      'react-hooks/invariant': 'error',
      'react-hooks/syntax': 'error',
      'react-hooks/rule-suppression': 'error',
    },
  },
])
