export function parseBody(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', (chunk) => {
      data += chunk;
    });
    req.on('end', () => {
      try {
        resolve(data ? JSON.parse(data) : {});
      } catch (err) {
        reject(err);
      }
    });
    req.on('error', reject);
  });
}

export function json(res, status, body) {
  if (res.headersSent || res.writableEnded || res.destroyed) {
    return false;
  }
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(body));
  return true;
}

export function createHttpAuth(sidecarKey) {
  return {
    checkAuth(req, res) {
      if (!sidecarKey) return true;
      if (req.headers['x-sidecar-key'] !== sidecarKey) {
        json(res, 401, { error: 'Unauthorized' });
        return false;
      }
      return true;
    },

    isAuthenticatedRequest(req) {
      return !sidecarKey || req.headers['x-sidecar-key'] === sidecarKey;
    },
  };
}

export function parseWorkspaceId(value) {
  if (value === undefined || value === null || value === '') return null;
  const parsed = parseInt(String(value), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

export function requireWorkspaceId(res, body, url) {
  const workspaceId = parseWorkspaceId(body?.workspaceId ?? url.searchParams.get('workspaceId'));
  if (!workspaceId) {
    json(res, 400, { error: 'workspaceId required' });
    return null;
  }
  return workspaceId;
}

export function requireTempSessionId(res, body) {
  const tempSessionId = body?.tempSessionId;
  if (!tempSessionId) {
    json(res, 400, { error: 'tempSessionId required' });
    return null;
  }
  return String(tempSessionId);
}
