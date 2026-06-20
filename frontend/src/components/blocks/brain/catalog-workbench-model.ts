import type { CatalogWorkspaceProduct } from '@/lib/types'

export function readyCatalogProducts(products: CatalogWorkspaceProduct[]) {
  return products.filter((item) => item.index_state === 'ready' && !item.conflict_refs.length).length
}

export function uniqueCatalogSources(products: CatalogWorkspaceProduct[]) {
  return Array.from(new Set(products.flatMap((product) => product.source_refs)))
}
