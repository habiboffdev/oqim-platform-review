import { useEffect } from 'react' // eslint-disable-line no-restricted-imports -- this is the ONE place useEffect is allowed

/**
 * Run an effect exactly once on mount, with optional cleanup on unmount.
 * This is the ONLY sanctioned way to use useEffect in this codebase.
 *
 * For everything else:
 * - Derived state → compute inline (const x = derive(y))
 * - Data fetching → TanStack Query (useQuery)
 * - User actions → event handlers
 * - Reset on ID change → key prop on parent
 *
 * See AGENTS.md §1 for rationale.
 */
export function useMountEffect(effect: () => void | (() => void)) {
  useEffect(effect, []) // eslint-disable-line react-hooks/exhaustive-deps
}
