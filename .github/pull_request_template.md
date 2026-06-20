## Summary

<!-- What does this PR do? Link to the issue if applicable: Fixes #123 -->

## Type

<!-- Check one -->
- [ ] Feature (new functionality)
- [ ] Bug fix (non-breaking fix)
- [ ] Refactor (no behavior change)
- [ ] Chore (deps, CI, docs, config)

## Area

<!-- Check all that apply -->
- [ ] Backend
- [ ] Frontend
- [ ] GramJS sidecar
- [ ] Infrastructure / CI

## Changes

<!-- List the key changes. Be specific about what files/modules were modified and why. -->

-

## Testing

<!-- How was this tested? Check all that apply. -->
- [ ] Backend tests pass (`cd backend && python -m pytest tests/`)
- [ ] Frontend tests pass (`cd frontend && npx vitest run`)
- [ ] GramJS sidecar checks pass (`cd gramjs-sidecar && node --check index.js && node --test *.test.js routes/*.test.js`)
- [ ] Manual testing done (describe below)
- [ ] No tests needed (config/docs only)

## Pre-commit Checklist

<!-- From AGENTS.md §6 -->
- [ ] `python -m py_compile` on changed `.py` files
- [ ] `cd frontend && npx tsc --noEmit` (if frontend changed)
- [ ] `cd backend && python -m pytest tests/` (if backend changed)
- [ ] `cd frontend && npm run build` (if frontend changed)

## Screenshots / Logs

<!-- Optional: Add screenshots for UI changes or relevant log output -->

## Notes for Reviewers

<!-- Anything reviewers should pay special attention to? Security implications? Migration steps? -->
