import { Component, type ErrorInfo, type ReactNode } from 'react'
import { WarningCircle } from '@phosphor-icons/react'
import { Button } from '@/components/ui/button'
import { uz } from '@/lib/uz'

interface Props {
  children: ReactNode
  fallback?: ReactNode
}

interface State {
  hasError: boolean
  error?: Error
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback

      return (
        <div className="flex h-full min-h-[200px] flex-col items-center justify-center gap-4 p-8 text-center">
          <div className="rounded-xl bg-destructive/10 p-4">
            <WarningCircle size={28} weight="thin" className="text-destructive" />
          </div>
          <div>
            <h3 className="text-sm font-medium">{uz.common.error}</h3>
            <p className="mt-1 max-w-xs text-sm text-muted-foreground">
              {this.state.error?.message || uz.common.errorOccurred}
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => this.setState({ hasError: false, error: undefined })}
          >
            {uz.common.retry}
          </Button>
        </div>
      )
    }

    return this.props.children
  }
}
