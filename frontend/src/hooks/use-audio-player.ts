import { create } from 'zustand'

export type PlaybackKind = 'voice' | 'audio' | 'video_note'

export interface PlaybackItem {
  messageId: number
  url: string
  duration: number
  kind: PlaybackKind
  label: string
}

type PlaybackSource = 'audio' | 'element' | null

interface AudioPlayerState {
  queue: PlaybackItem[]
  activeMessageId: number | null
  activeKind: PlaybackKind | null
  activeLabel: string
  activeSource: PlaybackSource
  isPlaying: boolean
  currentTime: number
  duration: number
  playbackRate: number
  setQueue: (items: PlaybackItem[]) => void
  play: (
    messageId: number,
    url: string,
    duration: number,
    kind?: PlaybackKind,
    label?: string,
  ) => void
  playElement: (item: PlaybackItem, element: HTMLMediaElement) => void
  registerElement: (messageId: number, element: HTMLMediaElement) => void
  unregisterElement: (messageId: number) => void
  pause: () => void
  resume: () => void
  seek: (time: number) => void
  setPlaybackRate: (rate: number) => void
  playNext: () => void
  playPrevious: () => void
  onTimeUpdate: (time: number, duration?: number) => void
  onEnded: () => void
  clear: () => void
}

const audio = new Audio()
const registeredElements = new Map<number, HTMLMediaElement>()
const registeredElementCleanup = new Map<number, () => void>()

function defaultLabel(kind: PlaybackKind): string {
  switch (kind) {
    case 'audio':
      return 'Audio'
    case 'video_note':
      return 'Video note'
    case 'voice':
    default:
      return 'Voice'
  }
}

export const useAudioPlayer = create<AudioPlayerState>((set, get) => {
  const clearActiveState = () => {
    set({
      activeMessageId: null,
      activeKind: null,
      activeLabel: '',
      activeSource: null,
      isPlaying: false,
      currentTime: 0,
      duration: 0,
    })
  }

  const pauseCurrentSource = () => {
    const state = get()
    if (state.activeSource === 'audio') {
      audio.pause()
      return
    }
    if (state.activeSource === 'element' && state.activeMessageId != null) {
      registeredElements.get(state.activeMessageId)?.pause()
    }
  }

  const playQueueItem = (item: PlaybackItem): boolean => {
    if (item.kind === 'video_note') {
      const element = registeredElements.get(item.messageId)
      if (!element) return false
      get().playElement(item, element)
      return true
    }
    get().play(item.messageId, item.url, item.duration, item.kind, item.label)
    return true
  }

  return {
    queue: [],
    activeMessageId: null,
    activeKind: null,
    activeLabel: '',
    activeSource: null,
    isPlaying: false,
    currentTime: 0,
    duration: 0,
    playbackRate: 1,

    setQueue: (items) => {
      set({ queue: items })
      const state = get()
      if (
        state.activeMessageId != null
        && !items.some((item) => item.messageId === state.activeMessageId)
      ) {
        pauseCurrentSource()
        clearActiveState()
      }
    },

    play: (messageId, url, duration, kind = 'voice', label = defaultLabel(kind)) => {
      if (!url) return

      const state = get()
      const isSameAudio = state.activeSource === 'audio' && state.activeMessageId === messageId

      if (isSameAudio) {
        if (state.isPlaying) {
          audio.pause()
          set({ isPlaying: false })
          return
        }
        audio.playbackRate = state.playbackRate
        void audio.play().catch(() => {
          set({ isPlaying: false })
        })
        set({ isPlaying: true })
        return
      }

      pauseCurrentSource()

      if (audio.src !== url) {
        audio.src = url
      }
      audio.currentTime = 0
      audio.playbackRate = state.playbackRate

      void audio.play().catch(() => {
        set({ isPlaying: false })
      })

      set({
        activeMessageId: messageId,
        activeKind: kind,
        activeLabel: label,
        activeSource: 'audio',
        isPlaying: true,
        currentTime: 0,
        duration,
      })
    },

    playElement: (item, element) => {
      const state = get()
      const isSameElement = state.activeSource === 'element' && state.activeMessageId === item.messageId

      if (isSameElement) {
        if (!element.paused) {
          element.pause()
          return
        }
        element.playbackRate = state.playbackRate
        void element.play().catch(() => {
          set({ isPlaying: false })
        })
        return
      }

      pauseCurrentSource()

      element.playbackRate = state.playbackRate

      set({
        activeMessageId: item.messageId,
        activeKind: item.kind,
        activeLabel: item.label,
        activeSource: 'element',
        isPlaying: true,
        currentTime: element.currentTime,
        duration: item.duration || (Number.isFinite(element.duration) ? element.duration : 0),
      })

      void element.play().catch(() => {
        set({ isPlaying: false })
      })
    },

    registerElement: (messageId, element) => {
      registeredElementCleanup.get(messageId)?.()
      registeredElements.set(messageId, element)

      const syncState = () => {
        const state = get()
        if (state.activeSource !== 'element' || state.activeMessageId !== messageId) {
          return
        }

        set({
          currentTime: element.currentTime,
          duration: Number.isFinite(element.duration) && element.duration > 0
            ? element.duration
            : state.duration,
          isPlaying: !element.paused,
        })
      }

      const handleEnded = () => {
        const state = get()
        if (state.activeSource !== 'element' || state.activeMessageId !== messageId) {
          return
        }
        set({
          isPlaying: false,
          currentTime: Number.isFinite(element.duration) ? element.duration : state.duration,
        })
      }

      const handleRateChange = () => {
        const state = get()
        if (state.activeSource !== 'element' || state.activeMessageId !== messageId) {
          return
        }
        set({ playbackRate: element.playbackRate })
      }

      element.addEventListener('loadedmetadata', syncState)
      element.addEventListener('timeupdate', syncState)
      element.addEventListener('play', syncState)
      element.addEventListener('playing', syncState)
      element.addEventListener('pause', syncState)
      element.addEventListener('ended', handleEnded)
      element.addEventListener('ratechange', handleRateChange)

      registeredElementCleanup.set(messageId, () => {
        element.removeEventListener('loadedmetadata', syncState)
        element.removeEventListener('timeupdate', syncState)
        element.removeEventListener('play', syncState)
        element.removeEventListener('playing', syncState)
        element.removeEventListener('pause', syncState)
        element.removeEventListener('ended', handleEnded)
        element.removeEventListener('ratechange', handleRateChange)
      })
    },

    unregisterElement: (messageId) => {
      registeredElementCleanup.get(messageId)?.()
      registeredElementCleanup.delete(messageId)
      registeredElements.delete(messageId)

      const state = get()
      if (state.activeSource === 'element' && state.activeMessageId === messageId) {
        clearActiveState()
      }
    },

    pause: () => {
      const state = get()
      if (!state.activeSource) return
      if (state.activeSource === 'audio') {
        audio.pause()
        set({ isPlaying: false })
        return
      }
      registeredElements.get(state.activeMessageId ?? -1)?.pause()
    },

    resume: () => {
      const state = get()
      if (!state.activeSource) return
      if (state.activeSource === 'audio') {
        audio.playbackRate = state.playbackRate
        void audio.play().catch(() => {
          set({ isPlaying: false })
        })
        set({ isPlaying: true })
        return
      }
      const element = registeredElements.get(state.activeMessageId ?? -1)
      if (!element) return
      element.playbackRate = state.playbackRate
      void element.play().catch(() => {
        set({ isPlaying: false })
      })
    },

    seek: (time) => {
      const state = get()
      if (!state.activeSource) return
      if (state.activeSource === 'audio') {
        audio.currentTime = time
        set({ currentTime: time })
        return
      }
      const element = registeredElements.get(state.activeMessageId ?? -1)
      if (!element) return
      element.currentTime = time
      set({ currentTime: time })
    },

    setPlaybackRate: (rate) => {
      const normalizedRate = Number.isFinite(rate) && rate > 0 ? rate : 1
      set({ playbackRate: normalizedRate })

      const state = get()
      if (state.activeSource === 'audio') {
        audio.playbackRate = normalizedRate
        return
      }
      const element = registeredElements.get(state.activeMessageId ?? -1)
      if (element) {
        element.playbackRate = normalizedRate
      }
    },

    playNext: () => {
      const state = get()
      if (!state.queue.length || state.activeMessageId == null) return

      const currentIndex = state.queue.findIndex((item) => item.messageId === state.activeMessageId)
      if (currentIndex === -1) return

      for (let index = currentIndex + 1; index < state.queue.length; index += 1) {
        if (playQueueItem(state.queue[index])) {
          return
        }
      }
    },

    playPrevious: () => {
      const state = get()
      if (!state.queue.length || state.activeMessageId == null) return

      const currentIndex = state.queue.findIndex((item) => item.messageId === state.activeMessageId)
      if (currentIndex === -1) return

      for (let index = currentIndex - 1; index >= 0; index -= 1) {
        if (playQueueItem(state.queue[index])) {
          return
        }
      }
    },

    onTimeUpdate: (time, duration) => {
      set((state) => ({
        currentTime: time,
        duration: duration ?? state.duration,
      }))
    },

    onEnded: () => {
      const state = get()
      set({
        isPlaying: false,
        currentTime: state.duration,
      })
    },

    clear: () => {
      pauseCurrentSource()
      clearActiveState()
      set({ queue: [] })
    },
  }
})

audio.addEventListener('timeupdate', () => {
  const state = useAudioPlayer.getState()
  if (state.activeSource !== 'audio') return
  useAudioPlayer.getState().onTimeUpdate(audio.currentTime, Number.isFinite(audio.duration) ? audio.duration : undefined)
})

audio.addEventListener('loadedmetadata', () => {
  const state = useAudioPlayer.getState()
  if (state.activeSource !== 'audio') return
  useAudioPlayer.getState().onTimeUpdate(audio.currentTime, Number.isFinite(audio.duration) ? audio.duration : undefined)
})

audio.addEventListener('ended', () => {
  const state = useAudioPlayer.getState()
  if (state.activeSource !== 'audio') return
  useAudioPlayer.getState().onEnded()
})
