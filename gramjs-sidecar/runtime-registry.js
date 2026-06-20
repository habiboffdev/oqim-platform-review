export function createRuntime(key, workspaceId = null, tempSessionId = null) {
  return {
    key,
    workspaceId,
    tempSessionId,
    client: null,
    connectionState: 'disconnected',
    reconnectAttempts: 0,
    latestMe: null,
    telegramState: null,
    telegramConnector: null,
    telegramMethodQueues: null,
    sessionString: null,
    lastError: null,
    lastCatchUpAt: null,
    lastCatchUpCount: 0,
    lastCatchUpSuccessAt: null,
    lastInboundHotPathAt: null,
    lastInboundHotPathLatencyMs: null,
    lastInboundHotPathSource: null,
    lastLiveInboundHotPathAt: null,
    lastLiveInboundHotPathLatencyMs: null,
    handlersRegisteredAt: null,
    catchUpScheduledAt: null,
    catchUpStartedAt: null,
    catchUpFailureCount: 0,
    catchUpInFlight: false,
    catchUpTimer: null,
    gapRepairInFlight: false,
    gapRepairTimer: null,
    connectPromise: null,
    reconnectTimer: null,
    pendingPhoneCodeHash: null,
    handlersRegistered: false,
    qrAuthRunning: false,
    qrAuthStatus: 'idle',
    qrAuthError: null,
    qrAuthUser: null,
    latestQR: null,
    twoFaResolve: null,
    twoFaTimer: null,
  };
}

export function createRuntimeRegistry(bootstrapKey) {
  const runtimes = new Map();

  function getRuntime(key, workspaceId = null, tempSessionId = null) {
    if (!runtimes.has(key)) {
      runtimes.set(key, createRuntime(key, workspaceId, tempSessionId));
    }
    return runtimes.get(key);
  }

  return {
    size() {
      return runtimes.size;
    },

    list() {
      return [...runtimes.values()];
    },

    getByKey(key) {
      return runtimes.get(key);
    },

    staleWorkspaceRuntimes(activeWorkspaceIds = []) {
      const active = new Set(activeWorkspaceIds.map((workspaceId) => Number(workspaceId)));
      return [...runtimes.values()].filter((runtime) => (
        runtime.workspaceId && !active.has(Number(runtime.workspaceId))
      ));
    },

    deleteByKey(key) {
      return runtimes.delete(key);
    },

    getBootstrap() {
      return getRuntime(bootstrapKey, null);
    },

    getWorkspace(workspaceId) {
      return getRuntime(`ws:${workspaceId}`, workspaceId);
    },

    getTemp(tempSessionId) {
      return getRuntime(`temp:${tempSessionId}`, null, tempSessionId);
    },
  };
}
