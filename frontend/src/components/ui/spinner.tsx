import { CircleNotch } from "@phosphor-icons/react";
import type React from "react";
import { cn } from "@/lib/utils";

const spinnerSizeClass: Record<string, string> = {
  sm: "size-4",
  default: "size-5",
  lg: "size-6",
};

export function Spinner({
  className,
  size,
  ...props
}: React.ComponentProps<typeof CircleNotch>): React.ReactElement {
  const presetSize = typeof size === "string" ? spinnerSizeClass[size] : undefined;

  return (
    <CircleNotch
      aria-label="Loading"
      weight="thin"
      className={cn("animate-spin", presetSize, className)}
      role="status"
      size={presetSize ? undefined : size}
      {...props}
    />
  );
}
