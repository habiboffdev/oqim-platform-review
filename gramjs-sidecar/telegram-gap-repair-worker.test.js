import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { repairTelegramUpdateGap } from './telegram-gap-repair-worker.js';

const hotCursor = {
  scope: 'hot_path',
  channelId: '',
  pts: 10,
  seq: 20,
  qts: 0,
  telegramDate: 1_700_000_000,
  receivedAt: 100,
  appliedAt: 110,
  ageSeconds: 400,
  stale: true,
  degradedState: {},
};

describe('telegram gap repair worker', () => {
  it('repairs stale global cursors through updates.getDifference', async () => {
    const calls = [];
    const runtime = {
      workspaceId: 7,
      connectionState: 'connected',
      client: {
        invoke: async (request) => {
          calls.push({
            type: 'invoke',
            request: {
              className: request.className,
              pts: request.pts,
              date: request.date,
              qts: request.qts,
              ptsLimit: request.ptsLimit,
            },
          });
          return {
            className: 'updates.Difference',
            newMessages: [{ id: 55, chatId: 444, message: 'missed' }],
            users: [{ id: 555, firstName: 'Ali' }],
            chats: [{ id: 444, title: 'Ali' }],
            state: {
              pts: 15,
              seq: 22,
              qts: 0,
              date: 1_700_000_100,
            },
          };
        },
      },
    };

    const result = await repairTelegramUpdateGap({
      runtime,
      durableStateStore: {
        cursorFreshnessForWorkspace: async (workspaceId) => {
          assert.equal(workspaceId, 7);
          return { cursors: [hotCursor] };
        },
        rememberPeer: async (workspaceId, peer, source, receivedAt, peerKind) => {
          calls.push({ type: 'peer', workspaceId, peer, source, receivedAt, peerKind });
          return true;
        },
        rememberUpdateCursorState: async (input) => {
          calls.push({ type: 'cursor', input });
        },
      },
      runQueuedTelegramMethod: async (_runtime, meta, fn) => {
        calls.push({ type: 'queued', meta });
        return fn();
      },
      withRpcTimeout: async (promise, label) => {
        calls.push({ type: 'rpc', label });
        return promise;
      },
      forwardInboundMessage: async (_runtime, msg, options) => {
        calls.push({ type: 'message', msg, options });
        return true;
      },
      now: 500,
    });

    assert.deepEqual(result, {
      scannedCursors: 1,
      repairedMessages: 1,
      peers: 2,
      failedChats: 0,
      tooLong: false,
      paused: false,
    });
    assert.deepEqual(calls[0], {
      type: 'queued',
      meta: {
        methodClass: 'gap_repair',
        label: 'GET_DIFFERENCE_7',
        jobKind: 'gap_repair',
        jobKey: 'global',
        priority: 2,
        cursor: {
          pts: 10,
          qts: 0,
          date: 1_700_000_000,
        },
      },
    });
    assert.deepEqual(calls.find((call) => call.type === 'invoke').request, {
      className: 'updates.GetDifference',
      pts: 10,
      date: 1_700_000_000,
      qts: 0,
      ptsLimit: 100,
    });
    assert.deepEqual(calls.find((call) => call.type === 'message').options, {
      isHistorical: true,
      source: 'gap_repair',
    });
    assert.equal(calls.filter((call) => call.type === 'peer').length, 2);
    assert.deepEqual(calls.find((call) => call.type === 'cursor').input, {
      runtime,
      cursorScope: 'gap_repair',
      channelId: '',
      pts: 15,
      seq: 22,
      qts: 0,
      telegramDate: 1_700_000_100,
      degradedState: {},
      receivedAt: 500,
      appliedAt: 500,
    });
  });

  it('skips repair when no stale global cursor exists', async () => {
    const result = await repairTelegramUpdateGap({
      runtime: {
        workspaceId: 7,
        connectionState: 'connected',
        client: { invoke: async () => { throw new Error('should not invoke'); } },
      },
      durableStateStore: {
        cursorFreshnessForWorkspace: async () => ({ cursors: [{ ...hotCursor, stale: false }] }),
      },
    });

    assert.deepEqual(result, {
      scannedCursors: 1,
      repairedMessages: 0,
      peers: 0,
      failedChats: 0,
      tooLong: false,
      paused: false,
    });
  });

  it('repairs stale per-chat cursors through chat history even when unread catch-up misses them', async () => {
    const calls = [];
    const chatCursor = {
      scope: 'hot_path',
      channelId: '5924086090',
      pts: null,
      seq: null,
      qts: null,
      telegramDate: 1_700_000_010,
      receivedAt: 100,
      appliedAt: 100,
      ageSeconds: 400,
      stale: true,
      degradedState: {},
    };
    const runtime = {
      workspaceId: 7,
      connectionState: 'connected',
      client: {
        getMessages: async () => { throw new Error('background client expected'); },
      },
    };
    const backgroundClient = {
      getMessages: async (peer, options) => {
        calls.push({ type: 'getMessages', peer, options });
        return [
          { id: 54, chatId: 444, date: 1_700_000_000, message: 'old', out: false },
          { id: 55, chatId: 444, date: 1_700_000_020, message: 'missed', out: false },
          { id: 56, chatId: 444, date: 1_700_000_030, message: 'seller echo', out: true },
        ];
      },
    };

    const result = await repairTelegramUpdateGap({
      runtime,
      durableStateStore: {
        cursorFreshnessForWorkspace: async () => ({ cursors: [chatCursor] }),
        rememberUpdateCursorState: async (input) => {
          calls.push({ type: 'cursor', input });
        },
      },
      runQueuedTelegramMethod: async (_runtime, meta, fn) => {
        calls.push({ type: 'queued', meta });
        return fn();
      },
      withBackgroundClient: async (_runtime, fn) => fn(backgroundClient),
      withRpcTimeout: async (promise, label) => {
        calls.push({ type: 'rpc', label });
        return promise;
      },
      resolvePeer: async (_runtime, chatId, options) => {
        calls.push({ type: 'resolvePeer', chatId, options });
        return { peer: chatId };
      },
      forwardInboundMessage: async (_runtime, msg, options) => {
        calls.push({ type: 'message', msg, options });
        return true;
      },
      now: 500,
    });

    assert.deepEqual(result, {
      scannedCursors: 1,
      repairedMessages: 1,
      peers: 0,
      failedChats: 0,
      tooLong: false,
      paused: false,
    });
    assert.deepEqual(calls.find((call) => call.type === 'queued').meta, {
      methodClass: 'gap_repair',
      label: 'GET_CHAT_GAP_7_5924086090',
      jobKind: 'gap_repair',
      jobKey: 'chat:5924086090',
      priority: 2,
      cursor: {
        channelId: '5924086090',
        date: 1_700_000_010,
      },
    });
    assert.deepEqual(calls.find((call) => call.type === 'resolvePeer'), {
      type: 'resolvePeer',
      chatId: '5924086090',
      options: { workspaceId: 7, purpose: 'gap_repair' },
    });
    assert.deepEqual(calls.find((call) => call.type === 'getMessages'), {
      type: 'getMessages',
      peer: { peer: '5924086090' },
      options: { limit: 25 },
    });
    assert.deepEqual(
      calls.filter((call) => call.type === 'message').map((call) => call.msg.id),
      [55],
    );
    assert.deepEqual(calls.find((call) => call.type === 'message').options, {
      isHistorical: true,
      source: 'gap_repair',
    });
    assert.deepEqual(calls.find((call) => call.type === 'cursor').input, {
      runtime,
      cursorScope: 'hot_path',
      channelId: '5924086090',
      pts: null,
      seq: null,
      qts: null,
      telegramDate: 1_700_000_030,
      degradedState: {},
      receivedAt: 500,
      appliedAt: 500,
    });
  });

  it('leaves cursor state untouched when gap repair lane is paused', async () => {
    const result = await repairTelegramUpdateGap({
      runtime: {
        workspaceId: 7,
        connectionState: 'connected',
        client: { invoke: async () => { throw new Error('should not invoke'); } },
      },
      durableStateStore: {
        cursorFreshnessForWorkspace: async () => ({ cursors: [hotCursor] }),
        rememberUpdateCursorState: async () => { throw new Error('should not update cursor'); },
      },
      runQueuedTelegramMethod: async () => {
        throw Object.assign(new Error('TELEGRAM_QUEUE_PAUSED:gap_repair:11'), {
          code: 'TELEGRAM_QUEUE_PAUSED',
          retryAfter: 11,
        });
      },
    });

    assert.deepEqual(result, {
      scannedCursors: 1,
      repairedMessages: 0,
      peers: 0,
      failedChats: 0,
      tooLong: false,
      paused: true,
    });
  });

  it('continues per-chat repair when one stale peer cannot resolve', async () => {
    const calls = [];
    const staleChat = (channelId) => ({
      scope: 'hot_path',
      channelId,
      pts: null,
      seq: null,
      qts: null,
      telegramDate: 1_700_000_010,
      receivedAt: 100,
      appliedAt: 100,
      ageSeconds: 400,
      stale: true,
      degradedState: {},
    });

    const result = await repairTelegramUpdateGap({
      runtime: {
        workspaceId: 7,
        connectionState: 'connected',
        client: {},
      },
      durableStateStore: {
        cursorFreshnessForWorkspace: async () => ({
          cursors: [staleChat('bad'), staleChat('good')],
        }),
        rememberUpdateCursorState: async (input) => {
          calls.push({ type: 'cursor', input });
        },
      },
      runQueuedTelegramMethod: async (_runtime, meta, fn) => {
        calls.push({ type: 'queued', jobKey: meta.jobKey });
        return fn();
      },
      withBackgroundClient: async (_runtime, fn) => fn({
        getMessages: async () => [
          { id: 55, chatId: 444, date: 1_700_000_020, message: 'missed', out: false },
        ],
      }),
      resolvePeer: async (_runtime, chatId) => {
        if (chatId === 'bad') throw new Error('cannot resolve');
        return { peer: chatId };
      },
      forwardInboundMessage: async (_runtime, msg) => {
        calls.push({ type: 'message', id: msg.id });
        return true;
      },
      now: 500,
    });

    assert.deepEqual(result, {
      scannedCursors: 2,
      repairedMessages: 1,
      peers: 0,
      failedChats: 1,
      tooLong: false,
      paused: false,
    });
    assert.deepEqual(calls, [
      { type: 'queued', jobKey: 'chat:bad' },
      {
        type: 'cursor',
        input: {
          runtime: {
            workspaceId: 7,
            connectionState: 'connected',
            client: {},
          },
          cursorScope: 'hot_path',
          channelId: 'bad',
          pts: null,
          seq: null,
          qts: null,
          telegramDate: 1_700_000_010,
          degradedState: {
            repair_failed: true,
            repair_error: 'cannot resolve',
          },
          receivedAt: 500,
          appliedAt: 500,
        },
      },
      { type: 'queued', jobKey: 'chat:good' },
      { type: 'message', id: 55 },
      {
        type: 'cursor',
        input: {
          runtime: {
            workspaceId: 7,
            connectionState: 'connected',
            client: {},
          },
          cursorScope: 'hot_path',
          channelId: 'good',
          pts: null,
          seq: null,
          qts: null,
          telegramDate: 1_700_000_020,
          degradedState: {},
          receivedAt: 500,
          appliedAt: 500,
        },
      },
    ]);
  });
});
