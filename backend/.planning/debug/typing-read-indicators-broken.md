---
status: awaiting_human_verify
trigger: "Bridge commands mark:read and set:typing don't work. They're sent from backend to the Telegram Web K iframe via postMessage but don't execute."
created: 2026-04-12T00:00:00Z
updated: 2026-04-12T00:00:02Z
---

## Current Focus
<!-- OVERWRITE on each update - reflects NOW -->

hypothesis: CONFIRMED — readHistory({peerId}) called without maxId (defaults to 0), hits historyStorage.triedToReadMaxId >= 0 guard (true after first call), silently no-ops every time. Fix: pass messageId as maxId in mark:read command.
test: Traced execution path in oqimBridge.ts and appMessagesManager.ts
expecting: Fix will make readHistory actually reach the Telegram API call
next_action: Apply fix to telegram-bridge.ts (send maxId) and oqimBridge.ts (receive maxId)

## Symptoms
<!-- Written during gathering, then IMMUTABLE -->

expected: When backend sends mark:read / set:typing bridge commands via postMessage to the Telegram Web K iframe, the iframe should mark conversations as read and show typing indicators to the other user.
actual: Commands are received but don't execute. No typing indicators shown, no read receipts sent.
errors: Unknown — need to check iframe console for errors from toPeerId() or the bridge command handlers.
reproduction: Send a message from test account (+998339331309) to the seller account. Backend should trigger mark:read and set:typing but they don't work.
started: These commands were added in most recent session (Phase 5 production quality). They may have never worked correctly.

## Eliminated
<!-- APPEND only - prevents re-investigating -->

- hypothesis: toPeerId() fails on chatId format
  evidence: chatId is String(m.peerId) which is a stringified positive integer. String("12345").toPeerId() = (+("12345")).toPeerId() = 12345. Valid PeerId. No conversion error.
  timestamp: 2026-04-12T00:00:01Z

- hypothesis: cross-origin postMessage blocking
  evidence: postMessage with '*' target origin works cross-origin. iframe.contentWindow.postMessage is valid even cross-origin.
  timestamp: 2026-04-12T00:00:01Z

- hypothesis: managers not initialized when command arrives
  evidence: bridge is initialized after page.mount() in index.ts. rootScope.managers is set at index.ts:389. Commands arrive after a new message triggers history_multiappend, by which time managers are ready.
  timestamp: 2026-04-12T00:00:01Z

## Evidence
<!-- APPEND only - facts discovered -->

- timestamp: 2026-04-12T00:00:01Z
  checked: oqim-tweb/src/lib/oqimBridge.ts lines 98-114
  found: mark:read calls managers.appMessagesManager.readHistory({peerId}) with NO maxId
  implication: maxId defaults to 0 in the readHistory function signature

- timestamp: 2026-04-12T00:00:01Z
  checked: oqim-tweb/src/lib/appManagers/appMessagesManager.ts:6134-6172
  found: readHistory with maxId=0 hits guard `historyStorage.triedToReadMaxId >= maxId` where triedToReadMaxId is set to 0 after first call. 0 >= 0 = true → silent no-op every call after first.
  implication: mark:read NEVER reaches the Telegram API messages.readHistory call (after 1st message). Even 1st call may no-op if dialog isn't loaded as unread.

- timestamp: 2026-04-12T00:00:01Z
  checked: oqim-tweb/src/lib/appManagers/appMessagesManager.ts:6263
  found: historyStorage.triedToReadMaxId = maxId (set to 0 on first call that proceeds past the guard)
  implication: Permanent dedup block installed on first call, preventing all future calls

- timestamp: 2026-04-12T00:00:01Z
  checked: frontend/src/lib/telegram-bridge.ts:38-44
  found: payload.messageId is available (from the message:new event). Currently not included in mark:read command.
  implication: Fix is straightforward — pass messageId as maxId in mark:read command payload

- timestamp: 2026-04-12T00:00:01Z
  checked: oqim-tweb/src/lib/appManagers/utils/messageId/clearMessageId.ts
  found: local mid = server message ID for regular inbox messages (< MESSAGE_ID_OFFSET)
  implication: m.mid is safe to pass as maxId to readHistory

## Resolution
<!-- OVERWRITE as understanding evolves -->

root_cause: TWO bugs. (1) cli/config.py pointed TELEGRAM_WEB_DIR to telegram-web-k (no oqimBridge) instead of oqim-tweb (the fork). oqim dev start ran unmodified Web K that ignores mark:read/set:typing. (2) readHistory({peerId}) called without maxId — maxId defaults to 0, and appMessagesManager.ts:6170 guard `historyStorage.triedToReadMaxId >= maxId` (0 >= 0 = always true after 1st call) causes silent no-op on every call.

fix: (1) Updated cli/config.py TELEGRAM_WEB_DIR to point to oqim-tweb. (2) telegram-bridge.ts now passes messageId as maxId in mark:read command. oqimBridge.ts mark:read handler extracts maxId from payload. Rebuilt oqim-tweb dist (new file: oqimBridge-Ckl6TdbU.js).

verification: All 8 frontend telegram-bridge.test.ts tests pass (including 3 new tests for mark:read+maxId and set:typing). All 5 oqim-tweb mark:read/set:typing tests pass. readHistory call in new dist verified: readHistory({peerId:a,maxId:e??0}).

files_changed:
  - cli/config.py
  - frontend/src/lib/telegram-bridge.ts
  - frontend/src/lib/telegram-bridge.test.ts
  - oqim-tweb: src/lib/oqimBridge.ts
  - oqim-tweb: src/lib/oqimBridge.test.ts
  - oqim-tweb: dist/oqimBridge-Ckl6TdbU.js (rebuilt)
