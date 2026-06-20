export function activeConversationIdFromRoute(input: {
  pathname?: string
  param?: string | number | null
}): number | undefined {
  const pathMatch = input.pathname?.match(/^\/conversations\/(\d+)(?:\/)?$/)
  const rawId = pathMatch?.[1] ?? input.param
  const id = Number(rawId)

  return Number.isSafeInteger(id) && id > 0 ? id : undefined
}
