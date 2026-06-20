FROM node:22-alpine

WORKDIR /app

RUN apk add --no-cache python3 make g++

COPY package.json ./
RUN npm install --production

COPY index.js ./
COPY chat-filters.js ./
COPY event-outbox.js ./
COPY http-utils.js ./
COPY lazy-boot.js ./
COPY multi-dc-session.js ./
COPY runtime-registry.js ./
COPY session-store.js ./
COPY sidecar-status.js ./
COPY telegram-codec.js ./
COPY telegram-errors.js ./

EXPOSE 3100

HEALTHCHECK --interval=10s --timeout=5s --retries=3 \
  CMD wget -qO- http://localhost:3100/health || exit 1

CMD ["node", "index.js"]
