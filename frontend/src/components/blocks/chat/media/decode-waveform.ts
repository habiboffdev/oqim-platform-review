/**
 * Decode Telegram's 5-bit packed waveform byte array into amplitude values (0-31).
 * The media runtime stores waveform as list(audio_attr.waveform) -- an array of raw bytes (0-255).
 * Each 5-bit sample can span byte boundaries, so we read uint16 windows.
 *
 * @param encoded - Raw byte array from media_metadata.waveform (values 0-255)
 * @returns Array of amplitude values (0-31), typically ~63 samples
 */
export function decodeWaveform(encoded: number[] | undefined): number[] {
  if (!encoded || encoded.length === 0) return []

  const bitsCount = encoded.length * 8
  const valuesCount = Math.floor(bitsCount / 5)
  if (!valuesCount) return []

  const result: number[] = new Array(valuesCount)

  for (let i = 0; i < valuesCount; i++) {
    const byteIndex = Math.floor((i * 5) / 8)
    const bitShift = (i * 5) % 8
    const value = (encoded[byteIndex] ?? 0) + ((encoded[byteIndex + 1] ?? 0) << 8)
    result[i] = (value >> bitShift) & 0x1f
  }

  return result
}
