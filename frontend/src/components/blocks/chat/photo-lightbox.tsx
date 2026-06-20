import 'yet-another-react-lightbox/styles.css'
import 'yet-another-react-lightbox/plugins/counter.css'
import Lightbox from 'yet-another-react-lightbox'
import Zoom from 'yet-another-react-lightbox/plugins/zoom'
import Counter from 'yet-another-react-lightbox/plugins/counter'

interface PhotoLightboxProps {
  open: boolean
  index: number
  slides: { src: string }[]
  onClose: () => void
  onIndexChange: (index: number) => void
}

export function PhotoLightbox({ open, index, slides, onClose, onIndexChange }: PhotoLightboxProps) {
  return (
    <Lightbox
      open={open}
      close={onClose}
      index={index}
      slides={slides}
      plugins={[Zoom, Counter]}
      on={{ view: ({ index: i }) => onIndexChange(i) }}
      zoom={{ maxZoomPixelRatio: 3, scrollToZoom: true }}
      carousel={{ preload: 2 }}
    />
  )
}
