import { Api } from 'telegram';

export function buildWorkspaceRestoreTransports(sessionTransport, defaultTransport) {
  return [
    sessionTransport,
    defaultTransport,
    'tcp',
  ].filter((transport, index, transports) => (
    transport && transports.indexOf(transport) === index
  ));
}

export async function restoreWorkspaceSession({
  workspaceId,
  runtime,
  sessionRecord,
  transportCandidates,
  createClient,
  connectWithTimeout,
  withRpcTimeout,
  destroyRuntimeClient,
  sessionStore,
  registerEventHandlers,
  scheduleCatchUp,
  scheduleGapRepair = null,
  scheduleMediaHydration = null,
  scheduleSyncJobResume = null,
  scheduleReconnect,
  normalizeTelegramAuthError,
  runtimeLabel,
}) {
  const sessionString = sessionRecord.sessionString;
  if (!sessionString) {
    runtime.connectionState = 'disconnected';
    return false;
  }
  runtime.sessionString = sessionString;

  const failures = [];
  let sawRevokedSession = false;

  for (const transport of transportCandidates) {
    if (runtime.client) {
      await destroyRuntimeClient(runtime);
    }

    runtime.connectionState = 'connecting';
    runtime.transport = transport;
    // Restore must be read-only until Telegram proves this auth key is still
    // authorized. GramJS can call session.save() during connect/DC negotiation;
    // persisting that before getMe()/isUserAuthorized() succeeds can overwrite
    // the last known workspace session with a failed restore attempt.
    runtime.client = await createClient(
      sessionString,
      undefined,
      { transport },
    );

    try {
      await connectWithTimeout(runtime.client);

      let authorized = false;
      try {
        const me = await withRpcTimeout(
          runtime.client.getMe(),
          `GET_ME_CONNECT_${workspaceId}`,
        );
        runtime.latestMe = me;
        authorized = !!me;
      } catch {
        authorized = await withRpcTimeout(
          runtime.client.isUserAuthorized(),
          `IS_AUTHORIZED_${workspaceId}`,
        );
      }

      if (!authorized) {
        sawRevokedSession = true;
        failures.push(`${transport}:SESSION_REVOKED`);
        await destroyRuntimeClient(runtime);
        continue;
      }

      await withRpcTimeout(
        runtime.client.invoke(new Api.updates.GetState()),
        `GET_STATE_CONNECT_${workspaceId}`,
      );
      runtime.connectionState = 'connected';
      runtime.reconnectAttempts = 0;
      runtime.lastError = null;
      runtime.sessionString = await sessionStore.snapshotSession(runtime.client);
      await sessionStore.saveSessionString(workspaceId, runtime.sessionString, { transport });
      sessionStore.retargetRuntimeSession(runtime, sessionStore.persistenceTargetForRuntime(runtime));
      registerEventHandlers(runtime);
      scheduleCatchUp(runtime, 1000);
      if (scheduleSyncJobResume) {
        scheduleSyncJobResume(runtime, 1500);
      }
      if (scheduleGapRepair) {
        scheduleGapRepair(runtime, 1800);
      }
      if (scheduleMediaHydration) {
        scheduleMediaHydration(runtime, 2200);
      }
      console.log(`[GramJS] Restored ${runtimeLabel(runtime)} via ${transport}`);
      return true;
    } catch (err) {
      const normalized = normalizeTelegramAuthError(err);
      if (normalized.code === 'SESSION_REVOKED') {
        sawRevokedSession = true;
        failures.push(`${transport}:${normalized.code}`);
        console.warn(`[GramJS] ${runtimeLabel(runtime)} session revoked; waiting for reconnect`);
        await destroyRuntimeClient(runtime);
        continue;
      }
      failures.push(`${transport}:${err.message}`);
      console.error(`[GramJS] Failed to restore ${runtimeLabel(runtime)} via ${transport}:`, err.message);
    }
  }

  runtime.connectionState = 'disconnected';
  if (sawRevokedSession) {
    runtime.lastError = 'SESSION_REVOKED';
    return false;
  }
  runtime.lastError = failures.join(', ') || 'connect_failed';
  scheduleReconnect(runtime);
  return false;
}
