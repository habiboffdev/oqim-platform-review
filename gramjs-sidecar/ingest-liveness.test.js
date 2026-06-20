import assert from 'node:assert/strict';
import test from 'node:test';

import {
  INGEST_STALE_MS,
  MAX_CATCHUP_FAILURES,
  nextIngestAction,
} from './ingest-liveness.js';

const NOW = Date.parse('2026-05-24T20:00:00Z');
const connected = (over = {}) => ({
  workspaceId: 1,
  connectionState: 'connected',
  catchUpFailureCount: 0,
  lastCatchUpSuccessAt: new Date(NOW).toISOString(),
  ...over,
});

test('fresh connected session that just caught up needs nothing', () => {
  assert.equal(nextIngestAction(connected(), { now: NOW }), 'ok');
});

test('connected account with quiet live pump and fresh catch-up needs nothing', () => {
  assert.equal(
    nextIngestAction(
      connected({
        handlersRegisteredAt: new Date(NOW - 60_000).toISOString(),
        lastLiveInboundHotPathAt: null,
        lastCatchUpSuccessAt: new Date(NOW - 30_000).toISOString(),
      }),
      { now: NOW },
    ),
    'ok',
  );
});

test('connected account with quiet live pump and stale catch-up schedules background catch-up', () => {
  assert.equal(
    nextIngestAction(
      connected({
        handlersRegisteredAt: new Date(NOW - 60_000).toISOString(),
        lastLiveInboundHotPathAt: null,
        lastCatchUpSuccessAt: new Date(NOW - INGEST_STALE_MS - 1).toISOString(),
      }),
      { now: NOW },
    ),
    'catch_up',
  );
});

test('connected idle session with old live proof does not re-run recovery after recent catch-up', () => {
  assert.equal(
    nextIngestAction(
      connected({
        handlersRegisteredAt: new Date(NOW - 5 * 60_000).toISOString(),
        lastLiveInboundHotPathAt: new Date(NOW - INGEST_STALE_MS - 1).toISOString(),
        lastCatchUpSuccessAt: new Date(NOW - 30_000).toISOString(),
      }),
      { now: NOW },
    ),
    'ok',
  );
});

test('connected but never caught up -> catch_up', () => {
  assert.equal(
    nextIngestAction(connected({ lastCatchUpSuccessAt: null }), { now: NOW }),
    'catch_up',
  );
});

test('connected but last SUCCESSFUL catch-up is stale -> catch_up', () => {
  const stale = new Date(NOW - INGEST_STALE_MS - 1000).toISOString();
  assert.equal(
    nextIngestAction(connected({ lastCatchUpSuccessAt: stale }), { now: NOW }),
    'catch_up',
  );
});

test('repeated catch-up failures keep catch-up degraded without reconnecting live updates', () => {
  const stale = new Date(NOW - INGEST_STALE_MS - 1000).toISOString();
  assert.equal(
    nextIngestAction(
      connected({
        catchUpFailureCount: MAX_CATCHUP_FAILURES,
        lastCatchUpSuccessAt: stale,
      }),
      { now: NOW },
    ),
    'catch_up',
  );
});

test('stale in-flight catch-up is degraded background sync, not reconnect', () => {
  const startedAt = new Date(NOW - INGEST_STALE_MS - 1000).toISOString();
  assert.equal(
    nextIngestAction(
      connected({
        catchUpInFlight: true,
        catchUpStartedAt: startedAt,
        handlersRegisteredAt: new Date(NOW - 5 * 60_000).toISOString(),
        lastLiveInboundHotPathAt: new Date(NOW - 4 * 60_000).toISOString(),
      }),
      { now: NOW },
    ),
    'ok',
  );
});

test('a failed catch-up attempt must not look fresh — still drives recovery', () => {
  const stale = new Date(NOW - INGEST_STALE_MS - 1000).toISOString();
  assert.equal(
    nextIngestAction(
      connected({ lastCatchUpSuccessAt: stale, catchUpFailureCount: 1 }),
      { now: NOW },
    ),
    'catch_up',
  );
});

test('non-connected states are left to the reconnect logic', () => {
  for (const state of ['reconnecting', 'disconnected', 'failed']) {
    assert.equal(nextIngestAction(connected({ connectionState: state }), { now: NOW }), 'ok');
  }
});

test('runtimes with no workspace are ignored', () => {
  assert.equal(nextIngestAction(connected({ workspaceId: null }), { now: NOW }), 'ok');
  assert.equal(nextIngestAction(null, { now: NOW }), 'ok');
});
