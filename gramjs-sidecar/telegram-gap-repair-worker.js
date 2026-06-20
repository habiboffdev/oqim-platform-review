import { Api } from 'telegram';

import { TELEGRAM_QUEUE_PAUSED } from './telegram-method-queue.js';

function emptyResult(scannedCursors = 0) {
  return {
    scannedCursors,
    repairedMessages: 0,
    peers: 0,
    failedChats: 0,
    tooLong: false,
    paused: false,
  };
}

function isConnectedRuntime(runtime) {
  return runtime?.workspaceId && runtime.connectionState === 'connected' && runtime.client;
}

function repairCursor(cursors = []) {
  return cursors.find((cursor) => (
    cursor.scope === 'hot_path'
    && !cursor.channelId
    && cursor.stale
    && cursor.pts != null
    && cursor.telegramDate != null
  ));
}

function repairChatCursors(cursors = []) {
  return cursors.filter((cursor) => (
    cursor.scope === 'hot_path'
    && cursor.channelId
    && cursor.stale
    && cursor.telegramDate != null
  ));
}

function differenceState(result, cursor) {
  if (result?.className === 'updates.DifferenceTooLong') {
    return {
      state: {
        pts: result.pts,
        seq: cursor.seq,
        qts: cursor.qts,
        date: cursor.telegramDate,
      },
      degradedState: { tooLong: true },
      tooLong: true,
    };
  }
  const state = result?.state || result?.intermediateState || null;
  if (state) {
    return {
      state,
      degradedState: {},
      tooLong: false,
    };
  }
  if (result?.className === 'updates.DifferenceEmpty') {
    return {
      state: {
        pts: cursor.pts,
        seq: result.seq,
        qts: cursor.qts,
        date: result.date,
      },
      degradedState: {},
      tooLong: false,
    };
  }
  return {
    state: null,
    degradedState: {},
    tooLong: false,
  };
}

function truncateErrorMessage(err) {
  return String(err?.message || err || 'unknown_error').slice(0, 240);
}

async function markChatRepairDegraded({ durableStateStore, runtime, cursor, now, err }) {
  try {
    await durableStateStore?.rememberUpdateCursorState?.({
      runtime,
      cursorScope: 'hot_path',
      channelId: cursor.channelId,
      pts: cursor.pts ?? null,
      seq: cursor.seq ?? null,
      qts: cursor.qts ?? null,
      telegramDate: cursor.telegramDate ?? null,
      degradedState: {
        repair_failed: true,
        repair_error: truncateErrorMessage(err),
      },
      receivedAt: now,
      appliedAt: now,
    });
  } catch {
    // Gap repair must stay fail-open: a degraded cursor record is useful but
    // should never make another chat's recovery fail.
  }
}

export async function repairTelegramUpdateGap({
  runtime,
  durableStateStore,
  runQueuedTelegramMethod = async (_runtime, _meta, fn) => fn(),
  withRpcTimeout = async (promise) => promise,
  withBackgroundClient = null,
  resolvePeer = async (_runtime, chatId) => chatId,
  forwardInboundMessage,
  staleAfterSeconds = 300,
  ptsLimit = 100,
  perChatLimit = 25,
  now = Date.now() / 1000,
} = {}) {
  if (!isConnectedRuntime(runtime) || !durableStateStore?.cursorFreshnessForWorkspace) {
    return emptyResult();
  }

  const freshness = await durableStateStore.cursorFreshnessForWorkspace(
    runtime.workspaceId,
    { now, staleAfterSeconds },
  );
  const cursors = freshness.cursors || [];
  const cursor = repairCursor(cursors);
  const chatCursors = repairChatCursors(cursors);
  if (!cursor && !chatCursors.length) {
    return emptyResult(cursors.length);
  }

  let scannedCursors = cursors.length;
  let totalRepairedMessages = 0;
  let totalPeers = 0;
  let failedChats = 0;
  let anyTooLong = false;

  try {
    for (const chatCursor of chatCursors) {
      try {
        const result = await repairChatHistoryGap({
          runtime,
          durableStateStore,
          runQueuedTelegramMethod,
          withRpcTimeout,
          withBackgroundClient,
          resolvePeer,
          forwardInboundMessage,
          cursor: chatCursor,
          perChatLimit,
          now,
        });
        totalRepairedMessages += result.repairedMessages;
        totalPeers += result.peers;
      } catch (err) {
        await markChatRepairDegraded({
          durableStateStore,
          runtime,
          cursor: chatCursor,
          now,
          err,
        });
        failedChats += 1;
      }
    }

    if (!cursor) {
      return {
        scannedCursors,
        repairedMessages: totalRepairedMessages,
        peers: totalPeers,
        failedChats,
        tooLong: anyTooLong,
        paused: false,
      };
    }

    const globalResult = await runQueuedTelegramMethod(
      runtime,
      {
        methodClass: 'gap_repair',
        label: `GET_DIFFERENCE_${runtime.workspaceId}`,
        jobKind: 'gap_repair',
        jobKey: 'global',
        priority: 2,
        cursor: {
          pts: cursor.pts,
          qts: cursor.qts || 0,
          date: cursor.telegramDate,
        },
      },
      async () => {
        const runDifference = async (telegramClient) => withRpcTimeout(
          telegramClient.invoke(new Api.updates.GetDifference({
            pts: cursor.pts,
            date: cursor.telegramDate,
            qts: cursor.qts || 0,
            ptsLimit,
          })),
          `GET_DIFFERENCE_${runtime.workspaceId}`,
        );
        const difference = withBackgroundClient
          ? await withBackgroundClient(runtime, runDifference)
          : await runDifference(runtime.client);

        let peers = 0;
        for (const user of difference?.users || []) {
          if (await durableStateStore.rememberPeer?.(
            runtime.workspaceId,
            user,
            'gap_repair',
            now,
            'user',
            runtime,
          )) {
            peers += 1;
          }
        }
        for (const chat of difference?.chats || []) {
          if (await durableStateStore.rememberPeer?.(
            runtime.workspaceId,
            chat,
            'gap_repair',
            now,
            'chat',
            runtime,
          )) {
            peers += 1;
          }
        }

        let repairedMessages = 0;
        for (const message of difference?.newMessages || []) {
          if (await forwardInboundMessage?.(runtime, message, {
            isHistorical: true,
            source: 'gap_repair',
          })) {
            repairedMessages += 1;
          }
        }

        const { state, degradedState, tooLong } = differenceState(difference, cursor);
        if (state) {
          await durableStateStore.rememberUpdateCursorState?.({
            runtime,
            cursorScope: 'gap_repair',
            channelId: '',
            pts: state.pts ?? null,
            seq: state.seq ?? null,
            qts: state.qts ?? null,
            telegramDate: state.date ?? null,
            degradedState,
            receivedAt: now,
            appliedAt: now,
          });
        }

        return {
          scannedCursors: cursors.length,
          repairedMessages,
          peers,
          tooLong,
          paused: false,
        };
      },
    );
    scannedCursors = globalResult.scannedCursors;
    totalRepairedMessages += globalResult.repairedMessages;
    totalPeers += globalResult.peers;
    anyTooLong = globalResult.tooLong;
    return {
      scannedCursors,
      repairedMessages: totalRepairedMessages,
      peers: totalPeers,
      failedChats,
      tooLong: anyTooLong,
      paused: false,
    };
  } catch (err) {
    if (err?.code === TELEGRAM_QUEUE_PAUSED) {
      return {
        ...emptyResult(scannedCursors),
        paused: true,
      };
    }
    throw err;
  }
}

async function repairChatHistoryGap({
  runtime,
  durableStateStore,
  runQueuedTelegramMethod,
  withRpcTimeout,
  withBackgroundClient,
  resolvePeer,
  forwardInboundMessage,
  cursor,
  perChatLimit,
  now,
}) {
  return runQueuedTelegramMethod(
    runtime,
    {
      methodClass: 'gap_repair',
      label: `GET_CHAT_GAP_${runtime.workspaceId}_${cursor.channelId}`,
      jobKind: 'gap_repair',
      jobKey: `chat:${cursor.channelId}`,
      priority: 2,
      cursor: {
        channelId: cursor.channelId,
        date: cursor.telegramDate,
      },
    },
    async () => {
      const fetchMessages = async (telegramClient) => {
        const peer = await resolvePeer(
          { ...runtime, client: telegramClient },
          cursor.channelId,
          { workspaceId: runtime.workspaceId, purpose: 'gap_repair' },
        );
        return withRpcTimeout(
          telegramClient.getMessages(peer, { limit: perChatLimit }),
          `GET_CHAT_GAP_${runtime.workspaceId}_${cursor.channelId}`,
        );
      };
      const messages = withBackgroundClient
        ? await withBackgroundClient(runtime, fetchMessages)
        : await fetchMessages(runtime.client);

      const orderedMessages = [...(messages || [])]
        .filter((message) => message?.id && Number(message.date || 0) >= Number(cursor.telegramDate || 0))
        .sort((a, b) => (a.date || 0) - (b.date || 0) || (a.id || 0) - (b.id || 0));

      let repairedMessages = 0;
      let latestDate = cursor.telegramDate || null;
      for (const message of orderedMessages) {
        latestDate = Math.max(Number(latestDate || 0), Number(message.date || 0)) || latestDate;
        if (message.out) {
          continue;
        }
        if (await forwardInboundMessage?.(runtime, message, {
          isHistorical: true,
          source: 'gap_repair',
        })) {
          repairedMessages += 1;
        }
      }

      if (latestDate != null) {
        await durableStateStore.rememberUpdateCursorState?.({
          runtime,
          cursorScope: 'hot_path',
          channelId: cursor.channelId,
          pts: cursor.pts ?? null,
          seq: cursor.seq ?? null,
          qts: cursor.qts ?? null,
          telegramDate: latestDate,
          degradedState: {},
          receivedAt: now,
          appliedAt: now,
        });
      }

      return {
        repairedMessages,
        peers: 0,
      };
    },
  );
}
