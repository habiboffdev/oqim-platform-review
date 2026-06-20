# devl.dev / coss UI Source Copies

This folder stores exact copied source from the public devl.dev shadcn registry.
The active OQIM app can adapt from these files, but these raw showcase files are
kept outside `src` because the upstream examples use `lucide-react` and demo
data that do not match this repo's runtime and lint rules.

Source commands used:

```bash
npx shadcn@latest add https://www.devl.dev/r/layouts/three-pane.json
npx shadcn@latest add https://www.devl.dev/r/layouts/app-shell.json
npx shadcn@latest add https://www.devl.dev/r/layouts/docs-tree.json
npx shadcn@latest add https://www.devl.dev/r/settings/integrations.json
npx shadcn@latest add https://www.devl.dev/r/tables/inventory.json
npx shadcn@latest add https://www.devl.dev/r/cards/product.json
npx shadcn@latest add https://www.devl.dev/r/cards/stat-tile.json
npx shadcn@latest add https://www.devl.dev/r/filters/toolbar.json
npx shadcn@latest add https://www.devl.dev/r/filters/chips.json
npx shadcn@latest add https://www.devl.dev/r/empty-states/no-results.json
npx shadcn@latest add https://www.devl.dev/r/modals/upload-files.json
npx shadcn@latest add https://www.devl.dev/r/dashboards/metrics-overview.json
```

Adaptation rule:

- Preserve layout, density, spacing, and interaction patterns from these source
  files.
- Replace demo data with canonical Business Brain / AutoCRM projections.
- Replace upstream icons with repo-approved Phosphor icons in compiled `src`.
- Do not let copied demo state become product truth.
