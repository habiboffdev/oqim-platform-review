import { PromisedWebSockets } from 'telegram/extensions/PromisedWebSockets.js';

export const DC_IP_TO_ID = {
  '149.154.175.53': 1,
  '149.154.175.50': 1,
  '149.154.167.40': 2,
  '149.154.167.41': 2,
  '149.154.167.50': 2,
  '149.154.167.51': 2,
  '149.154.175.100': 3,
  '149.154.167.91': 4,
  '149.154.167.92': 4,
  '149.154.171.5': 5,
};

export function telegramWebDcIdForIp(ip) {
  return DC_IP_TO_ID[ip] || 4;
}

export class TelegramWebSockets extends PromisedWebSockets {
  getWebSocketLink(ip, port, testServers = false) {
    const dcId = telegramWebDcIdForIp(ip);
    const suffix = testServers ? '_test' : '';
    const url = `wss://kws${dcId}.web.telegram.org/apiws${suffix}`;
    console.log(`[WSS] Connecting to ${url} (DC${dcId}, was ${ip}:${port})`);
    return url;
  }
}
