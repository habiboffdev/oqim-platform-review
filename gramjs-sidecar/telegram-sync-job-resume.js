function isDialogShellJob(job) {
  return job?.jobKind === 'dialog_sync' && job?.jobKey === 'dialog_shells';
}

function isUnreadCatchUpJob(job) {
  return job?.jobKind === 'unread_catchup';
}

function isGapRepairJob(job) {
  return job?.jobKind === 'gap_repair';
}

function isMediaHydrationJob(job) {
  return job?.jobKind === 'media_hydration';
}

export async function resumeTelegramSyncJobs({
  runtime,
  syncJobStore,
  syncDialogShells,
  scheduleCatchUp,
  scheduleGapRepair,
  scheduleMediaHydration,
  runtimeLabel = (targetRuntime) => `workspace:${targetRuntime?.workspaceId ?? 'unknown'}`,
} = {}) {
  if (!runtime?.workspaceId || !syncJobStore?.listResumableJobs) {
    return { jobs: 0, actions: 0 };
  }

  let jobs = [];
  try {
    jobs = await syncJobStore.listResumableJobs(runtime.workspaceId);
  } catch (err) {
    runtime.lastSyncJobResumeError = err.message || String(err);
    console.warn(`[SyncJobs] Resume lookup failed for ${runtimeLabel(runtime)}:`, runtime.lastSyncJobResumeError);
    return { jobs: 0, actions: 0 };
  }

  let actions = 0;
  runtime.lastSyncJobResumeCount = jobs.length;
  runtime.lastSyncJobResumeAt = new Date().toISOString();
  runtime.lastSyncJobResumeError = null;

  if (jobs.some(isDialogShellJob) && syncDialogShells) {
    try {
      await syncDialogShells(runtime);
      actions += 1;
    } catch (err) {
      runtime.lastSyncJobResumeError = err.message || String(err);
      console.warn(`[SyncJobs] Dialog-shell resume failed for ${runtimeLabel(runtime)}:`, runtime.lastSyncJobResumeError);
    }
  }

  if (jobs.some(isUnreadCatchUpJob) && scheduleCatchUp) {
    scheduleCatchUp(runtime, 0);
    actions += 1;
  }

  if (jobs.some(isGapRepairJob) && scheduleGapRepair) {
    scheduleGapRepair(runtime, 0);
    actions += 1;
  }

  if (jobs.some(isMediaHydrationJob) && scheduleMediaHydration) {
    scheduleMediaHydration(runtime, 0);
    actions += 1;
  }

  runtime.lastSyncJobResumeActions = actions;
  return { jobs: jobs.length, actions };
}
