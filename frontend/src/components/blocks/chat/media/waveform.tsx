interface WaveformProps {
  samples: number[]
  progress: number
  height?: number
  playedColor?: string
  unplayedColor?: string
  onSeek?: (ratio: number) => void
}

const BAR_WIDTH = 2
const BAR_GAP = 2
const MIN_BAR_HEIGHT = 2
const FLAT_BAR_COUNT = 40

export function Waveform({
  samples,
  progress,
  height,
  playedColor = 'var(--tg-primary)',
  unplayedColor = 'rgba(51,144,236,0.3)',
  onSeek,
}: WaveformProps) {
  const MAX_BAR_HEIGHT = height ?? 28

  const bars =
    samples.length === 0
      ? Array.from({ length: FLAT_BAR_COUNT }, () => MIN_BAR_HEIGHT)
      : samples.map(
          (sample) =>
            MIN_BAR_HEIGHT + (sample / 31) * (MAX_BAR_HEIGHT - MIN_BAR_HEIGHT),
        )

  const svgWidth = bars.length * (BAR_WIDTH + BAR_GAP) - BAR_GAP
  const progressIndex = Math.floor(progress * bars.length)

  const handleClick = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!onSeek) return
    const rect = e.currentTarget.getBoundingClientRect()
    const ratio = Math.max(
      0,
      Math.min(1, (e.clientX - rect.left) / rect.width),
    )
    onSeek(ratio)
  }

  return (
    <svg
      width={svgWidth}
      height={MAX_BAR_HEIGHT}
      viewBox={`0 0 ${svgWidth} ${MAX_BAR_HEIGHT}`}
      onClick={handleClick}
      style={{ display: 'block' }}
      role="slider"
      aria-label="Ijro holati"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(progress * 100)}
    >
      {bars.map((barHeight, i) => {
        const x = i * (BAR_WIDTH + BAR_GAP)
        const y = MAX_BAR_HEIGHT - barHeight
        const fill = i < progressIndex ? playedColor : unplayedColor
        return (
          <rect
            key={i}
            x={x}
            y={y}
            width={BAR_WIDTH}
            height={barHeight}
            rx={1}
            fill={fill}
          />
        )
      })}
    </svg>
  )
}
