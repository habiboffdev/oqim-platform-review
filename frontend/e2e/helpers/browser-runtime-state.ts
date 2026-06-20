import type { Page } from '@playwright/test'

const STALE_MARKER = 'RuntimeZeroStaleMuxlisaChatShouldNotSurvive'
const STALE_LOCAL_KEY = 'oqim:runtime-zero:stale-chat'
const STALE_SESSION_KEY = 'oqim:runtime-zero:stale-session'
const STALE_CACHE_NAME = 'oqim-runtime-zero-stale-cache'
const STALE_CACHE_URL = '/runtime-zero-stale-chat'
const STALE_DB_NAME = 'oqim-runtime-zero-stale-db'
const STALE_DB_STORE = 'messages'

export type BrowserRuntimeState = {
  localValue: string | null
  sessionValue: string | null
  hasCache: boolean
  hasIndexedDb: boolean
  serviceWorkerCount: number
}

type RuntimeZeroConfig = {
  marker: string
  localKey: string
  sessionKey: string
  cacheName: string
  cacheUrl: string
  dbName: string
  storeName: string
}

const config: RuntimeZeroConfig = {
  marker: STALE_MARKER,
  localKey: STALE_LOCAL_KEY,
  sessionKey: STALE_SESSION_KEY,
  cacheName: STALE_CACHE_NAME,
  cacheUrl: STALE_CACHE_URL,
  dbName: STALE_DB_NAME,
  storeName: STALE_DB_STORE,
}

export function staleBrowserMarker() {
  return STALE_MARKER
}

export async function seedStaleBrowserRuntimeState(page: Page) {
  await page.evaluate(async (runtimeConfig) => {
    localStorage.setItem(runtimeConfig.localKey, runtimeConfig.marker)
    sessionStorage.setItem(runtimeConfig.sessionKey, runtimeConfig.marker)

    if ('caches' in window) {
      const cache = await caches.open(runtimeConfig.cacheName)
      await cache.put(
        runtimeConfig.cacheUrl,
        new Response(runtimeConfig.marker, { headers: { 'content-type': 'text/plain' } }),
      )
    }

    await new Promise<void>((resolve, reject) => {
      const request = indexedDB.open(runtimeConfig.dbName, 1)
      request.onupgradeneeded = () => {
        request.result.createObjectStore(runtimeConfig.storeName, { keyPath: 'id' })
      }
      request.onerror = () => reject(request.error)
      request.onsuccess = () => {
        const db = request.result
        const transaction = db.transaction(runtimeConfig.storeName, 'readwrite')
        transaction.objectStore(runtimeConfig.storeName).put({
          id: 'stale-chat',
          preview: runtimeConfig.marker,
        })
        transaction.oncomplete = () => {
          db.close()
          resolve()
        }
        transaction.onerror = () => {
          db.close()
          reject(transaction.error)
        }
      }
    })
  }, config)
}

export async function clearBrowserRuntimeState(page: Page) {
  await page.evaluate(async (runtimeConfig) => {
    localStorage.clear()
    sessionStorage.clear()

    if ('caches' in window) {
      const cacheNames = await caches.keys()
      await Promise.all(cacheNames.map((cacheName) => caches.delete(cacheName)))
    }

    const databaseNames = new Set<string>([runtimeConfig.dbName])
    if ('databases' in indexedDB && typeof indexedDB.databases === 'function') {
      const databases = await indexedDB.databases()
      for (const database of databases) {
        if (database.name) databaseNames.add(database.name)
      }
    }

    await Promise.all(
      [...databaseNames].map(
        (databaseName) =>
          new Promise<void>((resolve, reject) => {
            const request = indexedDB.deleteDatabase(databaseName)
            request.onsuccess = () => resolve()
            request.onerror = () => reject(request.error)
            request.onblocked = () => reject(new Error(`IndexedDB delete blocked for ${databaseName}`))
          }),
      ),
    )

    if ('serviceWorker' in navigator) {
      const registrations = await navigator.serviceWorker.getRegistrations()
      await Promise.all(registrations.map((registration) => registration.unregister()))
    }
  }, config)
}

export async function readBrowserRuntimeState(page: Page): Promise<BrowserRuntimeState> {
  return page.evaluate(async (runtimeConfig) => {
    const cacheNames = 'caches' in window ? await caches.keys() : []
    const databaseNames =
      'databases' in indexedDB && typeof indexedDB.databases === 'function'
        ? (await indexedDB.databases()).map((database) => database.name).filter(Boolean)
        : []
    const registrations =
      'serviceWorker' in navigator ? await navigator.serviceWorker.getRegistrations() : []

    return {
      localValue: localStorage.getItem(runtimeConfig.localKey),
      sessionValue: sessionStorage.getItem(runtimeConfig.sessionKey),
      hasCache: cacheNames.includes(runtimeConfig.cacheName),
      hasIndexedDb: databaseNames.includes(runtimeConfig.dbName),
      serviceWorkerCount: registrations.length,
    }
  }, config)
}
