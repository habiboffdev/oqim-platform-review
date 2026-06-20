import { Api } from 'telegram';
import { DeletedMessage } from 'telegram/events/DeletedMessage.js';
import { EditedMessage } from 'telegram/events/EditedMessage.js';
import { NewMessage, Raw } from 'telegram/events/index.js';

export function startTelegramUpdatePump(runtime, {
  forwardInboundMessage,
  forwardEditedMessage,
  forwardDeletedMessage,
  forwardTypingSignal = null,
  scheduleReconnect,
  runtimeLabel,
  nowSeconds = () => Date.now() / 1000,
} = {}) {
  if (!runtime?.client || runtime.handlersRegistered) return false;

  runtime.client.addEventHandler(async (event) => {
    const telegramUpdateReceivedAt = nowSeconds();
    const msg = event.message;
    if (!msg) return;
    await forwardInboundMessage(runtime, msg, { telegramUpdateReceivedAt });
  }, new NewMessage({}));

  runtime.client.addEventHandler(async (event) => {
    const msg = event.message;
    if (!msg) return;
    try {
      await forwardEditedMessage(runtime, msg);
    } catch (err) {
      console.warn(`[Edit] Forward failed for ${runtimeLabel(runtime)}:`, err.message);
    }
  }, new EditedMessage({}));

  runtime.client.addEventHandler(async (event) => {
    if (!event.deletedIds?.length) return;
    try {
      await forwardDeletedMessage(runtime, event);
    } catch (err) {
      console.warn(`[Delete] Forward failed for ${runtimeLabel(runtime)}:`, err.message);
    }
  }, new DeletedMessage({}));

  // Private-chat "typing…" signals hold the backend turn lease so message
  // bursts (salom + the real question) coalesce into one agent turn.
  if (forwardTypingSignal) {
    runtime.client.addEventHandler((update) => {
      if (!(update instanceof Api.UpdateUserTyping)) return;
      try {
        forwardTypingSignal(runtime, update);
      } catch (err) {
        // best-effort: typing signals must never disturb the pump
        console.warn(`[Typing] Forward failed for ${runtimeLabel(runtime)}:`, err.message);
      }
    }, new Raw({}));
  }

  // UpdatesTooLong means Telegram says this account fell behind the update
  // sequence. Reconnect here because this is a live-update transport failure,
  // unlike stale dialog/history/media sync.
  runtime.client.addEventHandler((update) => {
    if (!(update instanceof Api.UpdatesTooLong)) return;
    if (runtime.connectionState === 'connected') {
      console.warn(`[TelegramUpdatePump] Lost updates for ${runtimeLabel(runtime)}; reconnecting`);
      runtime.connectionState = 'disconnected';
      scheduleReconnect(runtime);
    }
  }, new Raw({}));

  runtime.handlersRegistered = true;
  runtime.handlersRegisteredAt = new Date().toISOString();
  runtime.updatePumpStartedAt = runtime.handlersRegisteredAt;
  return true;
}
