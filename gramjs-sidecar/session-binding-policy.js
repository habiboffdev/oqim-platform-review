export function telegramUserIdsMatch(left, right) {
  if (left === null || left === undefined || right === null || right === undefined) {
    return false;
  }
  return String(left) === String(right);
}

export function isHealthyWorkspaceSession(runtime) {
  return Boolean(runtime?.client && runtime.connectionState === 'connected');
}

