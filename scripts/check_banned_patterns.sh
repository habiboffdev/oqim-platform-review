#!/bin/bash
# Pre-commit hook: catch banned patterns before they land in the repo.
# Run manually: bash scripts/check_banned_patterns.sh
# Or install as hook: ln -s ../../scripts/check_banned_patterns.sh .git/hooks/pre-commit

ERRORS=0
STAGED=$(git diff --cached --name-only --diff-filter=ACM)

for f in $(echo "$STAGED" | grep '\.py$' | grep -v 'test_'); do
    if grep -qn 'genai\.Client()' "$f" 2>/dev/null; then
        echo "BANNED in $f: Use get_client() from app.brain.llm"
        ERRORS=$((ERRORS + 1))
    fi
    if grep -qn '::jsonb' "$f" 2>/dev/null; then
        echo "BANNED in $f: Use CAST(:param AS json) instead of ::jsonb (asyncpg breaks)"
        ERRORS=$((ERRORS + 1))
    fi
    if grep -qn 'InMemorySessionService' "$f" 2>/dev/null; then
        echo "BANNED in $f: Use DatabaseSessionService (sessions must persist across restarts)"
        ERRORS=$((ERRORS + 1))
    fi
done

for f in $(echo "$STAGED" | grep -E '\.(ts|tsx|json)$'); do
    if grep -qn '517[345]' "$f" 2>/dev/null; then
        echo "BANNED in $f: Found Vite default port (5173/5174/5175). Use 4200 for frontend."
        ERRORS=$((ERRORS + 1))
    fi
done

if [ "$ERRORS" -gt 0 ]; then
    echo ""
    echo "Found $ERRORS banned pattern(s). Fix them before committing."
    echo "See .claude/rules/banned-patterns.md for correct alternatives."
fi

exit $ERRORS
