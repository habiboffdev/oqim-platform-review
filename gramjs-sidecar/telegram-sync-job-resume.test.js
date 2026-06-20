import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { resumeTelegramSyncJobs } from './telegram-sync-job-resume.js';

describe('telegram sync job resume', () => {
  it('resumes background dialog and unread jobs from persisted records', async () => {
    const calls = [];
    const runtime = { workspaceId: 7 };
    const result = await resumeTelegramSyncJobs({
      runtime,
      syncJobStore: {
        listResumableJobs: async (workspaceId) => {
          assert.equal(workspaceId, 7);
          return [
            { jobKind: 'dialog_sync', jobKey: 'dialog_shells' },
            { jobKind: 'unread_catchup', jobKey: 'dialogs' },
            { jobKind: 'gap_repair', jobKey: 'global' },
            { jobKind: 'media_hydration', jobKey: 'pending_refs' },
            { jobKind: 'history_sync', jobKey: 'GET_MESSAGES_7' },
          ];
        },
      },
      syncDialogShells: async (targetRuntime) => {
        calls.push({ type: 'dialog_shells', workspaceId: targetRuntime.workspaceId });
      },
      scheduleCatchUp: (targetRuntime, delayMs) => {
        calls.push({ type: 'catch_up', workspaceId: targetRuntime.workspaceId, delayMs });
      },
      scheduleGapRepair: (targetRuntime, delayMs) => {
        calls.push({ type: 'gap_repair', workspaceId: targetRuntime.workspaceId, delayMs });
      },
      scheduleMediaHydration: (targetRuntime, delayMs) => {
        calls.push({ type: 'media_hydration', workspaceId: targetRuntime.workspaceId, delayMs });
      },
      runtimeLabel: (targetRuntime) => `workspace:${targetRuntime.workspaceId}`,
    });

    assert.deepEqual(result, { jobs: 5, actions: 4 });
    assert.deepEqual(calls, [
      { type: 'dialog_shells', workspaceId: 7 },
      { type: 'catch_up', workspaceId: 7, delayMs: 0 },
      { type: 'gap_repair', workspaceId: 7, delayMs: 0 },
      { type: 'media_hydration', workspaceId: 7, delayMs: 0 },
    ]);
    assert.equal(runtime.lastSyncJobResumeCount, 5);
    assert.equal(runtime.lastSyncJobResumeActions, 4);
    assert.equal(runtime.lastSyncJobResumeError, null);
  });
});
