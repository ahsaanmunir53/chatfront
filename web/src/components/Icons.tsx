// A small set of plain line icons drawn for Chatfront. Brand names are written out as text.
import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Icon({ size = 20, children, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  );
}

export const ChatIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 5.5h16v10H9l-5 4z" />
  </Icon>
);

export const SendIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 12l16-7-6 15-2.5-6.5z" />
  </Icon>
);

export const BackIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M15 5l-7 7 7 7" />
  </Icon>
);

export const ArrowIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </Icon>
);

export const CheckIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M5 12.5l4.5 4.5L19 7.5" />
  </Icon>
);

export const ClockIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="8" />
    <path d="M12 8v4.5l3 2" />
  </Icon>
);

export const ShieldIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 3l7 3v5.5c0 4.5-3 7.8-7 9.5-4-1.7-7-5-7-9.5V6z" />
    <path d="M9 12l2.2 2.2L15.5 10" />
  </Icon>
);

export const PinIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 21s-6-5.6-6-11a6 6 0 1112 0c0 5.4-6 11-6 11z" />
    <circle cx="12" cy="10" r="2.2" />
  </Icon>
);

export const GlobeIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M3.5 12h17M12 3.5c2.4 2.4 3.5 5.3 3.5 8.5s-1.1 6.1-3.5 8.5c-2.4-2.4-3.5-5.3-3.5-8.5S9.6 5.9 12 3.5z" />
  </Icon>
);

export const ChevronIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M7 10l5 5 5-5" />
  </Icon>
);
