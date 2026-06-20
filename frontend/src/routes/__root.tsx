import { Outlet } from '@tanstack/react-router'
import { Toaster } from 'sonner'
import { ErrorBoundary } from '@/components/primitives/error-boundary'

export function RootLayout() {
  return (
    <ErrorBoundary>
      <Outlet />
      <Toaster
        position="bottom-right"
        toastOptions={{
          style: {
            background: 'var(--color-background)',
            border: '1px solid var(--color-border)',
            color: 'var(--color-foreground)',
            fontSize: '13px',
          },
        }}
      />
    </ErrorBoundary>
  )
}
