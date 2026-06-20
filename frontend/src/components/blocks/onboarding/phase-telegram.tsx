import { PhoneAuth } from '@/components/blocks/onboarding/phone-auth'
import { Button } from '@/components/ui/button'
import { uz } from '@/lib/uz'

export function TelegramAuthStep({
  isReconnect,
  isSessionRevoked,
  isIdentityMismatch,
  isAlreadyConnected,
  onSkip,
  onSuccess,
}: {
  isReconnect: boolean
  isSessionRevoked: boolean
  isIdentityMismatch: boolean
  isAlreadyConnected: boolean
  onSkip?: () => void
  onSuccess: (user: { userId: string; phone: string; firstName: string; lastName: string; authMethod?: 'phone' | 'qr' }) => void
}) {
  return (
    <>
      <div className="mt-8 text-xs font-medium text-muted-foreground">
        Telegramni ulang
      </div>
      <h1 className="mt-2 text-3xl font-semibold leading-tight tracking-tight">
        {isReconnect
          ? isAlreadyConnected
            ? uz.onboarding.reconnectAlreadyConnectedTitle
            : uz.onboarding.reconnectTitle
          : uz.connect.phoneTitle}
      </h1>
      <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
        {isReconnect
          ? isAlreadyConnected
            ? uz.onboarding.reconnectAlreadyConnectedDesc
            : isIdentityMismatch
            ? uz.onboarding.reconnectIdentityMismatchDesc
            : isSessionRevoked
            ? uz.onboarding.reconnectRevokedDesc
            : uz.onboarding.reconnectDesc
          : uz.connect.phoneSubtitle}
      </p>
      {isAlreadyConnected ? (
        onSkip && (
          <div className="mt-8 flex justify-center">
            <Button type="button" onClick={onSkip}>
              {uz.onboarding.continueSetup}
            </Button>
          </div>
        )
      ) : (
        <div className="mt-8 flex justify-center">
          <PhoneAuth onSuccess={onSuccess} />
        </div>
      )}
      {onSkip && !isAlreadyConnected && (
        <div className="mt-5 flex justify-center">
          <Button type="button" variant="ghost" onClick={onSkip}>
            {uz.connect.connectLater}
          </Button>
        </div>
      )}
    </>
  )
}
