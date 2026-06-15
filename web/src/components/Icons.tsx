import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement> & {
  size?: number;
};

const sharedProps = {
  fill: "none",
  stroke: "currentColor",
  strokeLinecap: "round",
  strokeLinejoin: "round",
  strokeWidth: 1.7,
} as const;

export function StarMark({ size = 28, ...props }: IconProps) {
  return (
    <svg
      aria-hidden="true"
      height={size}
      viewBox="0 0 32 32"
      width={size}
      {...sharedProps}
      {...props}
    >
      <circle cx="16" cy="16" fill="currentColor" r="2.8" stroke="none" />
      <path d="M16 3.5v7M16 21.5v7M3.5 16h7M21.5 16h7" />
      <path d="m8.4 8.4 3.2 3.2M20.4 20.4l3.2 3.2M23.6 8.4l-3.2 3.2M11.6 20.4l-3.2 3.2" />
    </svg>
  );
}

export function UploadIcon({ size = 18, ...props }: IconProps) {
  return (
    <svg
      aria-hidden="true"
      height={size}
      viewBox="0 0 24 24"
      width={size}
      {...sharedProps}
      {...props}
    >
      <path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5" />
      <path d="M5 14v4.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V14" />
    </svg>
  );
}

export function ArrowIcon({ size = 18, ...props }: IconProps) {
  return (
    <svg
      aria-hidden="true"
      height={size}
      viewBox="0 0 24 24"
      width={size}
      {...sharedProps}
      {...props}
    >
      <path d="M5 12h13M14 7l5 5-5 5" />
    </svg>
  );
}

export function MenuIcon({
  open,
  size = 22,
  ...props
}: IconProps & { open: boolean }) {
  return (
    <svg
      aria-hidden="true"
      height={size}
      viewBox="0 0 24 24"
      width={size}
      {...sharedProps}
      {...props}
    >
      {open ? (
        <>
          <path d="m6 6 12 12" />
          <path d="M18 6 6 18" />
        </>
      ) : (
        <>
          <path d="M4 7h16" />
          <path d="M4 12h16" />
          <path d="M4 17h16" />
        </>
      )}
    </svg>
  );
}

export function OrbitIcon({ size = 28, ...props }: IconProps) {
  return (
    <svg
      aria-hidden="true"
      height={size}
      viewBox="0 0 32 32"
      width={size}
      {...sharedProps}
      {...props}
    >
      <circle cx="16" cy="16" fill="currentColor" r="2.5" stroke="none" />
      <ellipse cx="16" cy="16" rx="12" ry="5.7" />
      <ellipse cx="16" cy="16" rx="5.7" ry="12" transform="rotate(38 16 16)" />
    </svg>
  );
}

export function SparkIcon({ size = 28, ...props }: IconProps) {
  return (
    <svg
      aria-hidden="true"
      height={size}
      viewBox="0 0 32 32"
      width={size}
      {...sharedProps}
      {...props}
    >
      <path d="M16 3.5c.7 7.8 4.7 11.8 12.5 12.5-7.8.7-11.8 4.7-12.5 12.5C15.3 20.7 11.3 16.7 3.5 16 11.3 15.3 15.3 11.3 16 3.5Z" />
      <path d="M25.5 3.5c.2 2.2 1.3 3.3 3.5 3.5-2.2.2-3.3 1.3-3.5 3.5-.2-2.2-1.3-3.3-3.5-3.5 2.2-.2 3.3-1.3 3.5-3.5Z" />
    </svg>
  );
}

export function HistoryIcon({ size = 28, ...props }: IconProps) {
  return (
    <svg
      aria-hidden="true"
      height={size}
      viewBox="0 0 32 32"
      width={size}
      {...sharedProps}
      {...props}
    >
      <path d="M6.5 10.5A11 11 0 1 1 5 19" />
      <path d="M6.5 4.5v6h6" />
      <path d="M16 9.5V16l4 2.5" />
    </svg>
  );
}

export function InfoIcon({ size = 20, ...props }: IconProps) {
  return (
    <svg
      aria-hidden="true"
      height={size}
      viewBox="0 0 24 24"
      width={size}
      {...sharedProps}
      {...props}
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M12 10.5V16M12 7.5h.01" />
    </svg>
  );
}

