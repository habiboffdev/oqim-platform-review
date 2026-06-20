export async function discardRuntimeAuthorization({
  runtime,
  reason,
  destroyRuntimeClient,
  runtimeLabel,
  logger = console,
}) {
  if (!runtime.client) {
    return false;
  }

  logger.log?.(
    `[Sidecar] Discarded ${runtimeLabel(runtime)} Telegram login locally: ${reason}`,
  );
  await destroyRuntimeClient(runtime);
  return true;
}
