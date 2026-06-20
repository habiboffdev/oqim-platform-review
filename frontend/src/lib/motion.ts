export const spring = { type: "spring" as const, stiffness: 400, damping: 30 }
export const springGentle = { type: "spring" as const, stiffness: 300, damping: 25 }

export const fadeIn = {
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  exit: { opacity: 0 },
  transition: { duration: 0.15 },
}

export const slideUp = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: 8 },
  transition: springGentle,
}

export const slideInRight = {
  initial: { opacity: 0, x: 20 },
  animate: { opacity: 1, x: 0 },
  exit: { opacity: 0, x: 20 },
  transition: springGentle,
}

export const staggerContainer = {
  animate: { transition: { staggerChildren: 0.04 } },
}

export const staggerItem = {
  initial: { opacity: 0, y: 6 },
  animate: { opacity: 1, y: 0 },
}

export const hoverScale = {
  whileHover: { scale: 1.02 },
  whileTap: { scale: 0.98 },
  transition: spring,
}

export const slidePanel = {
  initial: { x: 520, opacity: 0 },
  animate: { x: 0, opacity: 1 },
  exit: { x: 520, opacity: 0 },
  transition: { type: "spring" as const, stiffness: 300, damping: 30 },
}

export const scalePulse = {
  initial: { scale: 0.8, opacity: 0 },
  animate: { scale: 1, opacity: 1 },
  transition: springGentle,
}

// ── Seller Agent reply presets ──────────────────────────

export const overlaySlideUp = {
  initial: { opacity: 0, y: 24, scale: 0.98 },
  animate: { opacity: 1, y: 0, scale: 1 },
  exit: { opacity: 0, y: 16, scale: 0.98 },
  transition: { type: "spring" as const, stiffness: 350, damping: 30 },
}

export const chipStagger = {
  animate: { transition: { staggerChildren: 0.05, delayChildren: 0.2 } },
}

export const chipItem = {
  initial: { opacity: 0, scale: 0.85, y: 4 },
  animate: { opacity: 1, scale: 1, y: 0 },
  transition: { type: "spring" as const, stiffness: 500, damping: 28 },
}

export const shimmerPulse = {
  animate: {
    opacity: [0.4, 0.7, 0.4],
    transition: { repeat: Infinity, duration: 1.5, ease: "easeInOut" as const },
  },
}

export const versionSlide = {
  initial: { opacity: 0, height: 0 },
  animate: { opacity: 1, height: "auto" },
  exit: { opacity: 0, height: 0 },
  transition: { type: "spring" as const, stiffness: 400, damping: 35 },
}

export const badgePop = {
  initial: { scale: 0, opacity: 0 },
  animate: { scale: 1, opacity: 1 },
  transition: { type: "spring" as const, stiffness: 600, damping: 20, delay: 0.1 },
}

export const ghostCheckmark = {
  initial: { scale: 0, opacity: 0, rotate: -45 },
  animate: { scale: 1, opacity: 1, rotate: 0 },
  transition: { type: "spring" as const, stiffness: 500, damping: 25 },
}
