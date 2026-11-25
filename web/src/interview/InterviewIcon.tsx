const paths = {
  brand: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20ZM16 8l-2.5 5.5L8 16l2.5-5.5L16 8Z",
  chat: "M8 17H5l-3 3V6a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2v3M22 13v8l-3-3h-7a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2Z",
  folder:
    "M3 18a2 2 0 0 1-1-2V6a2 2 0 0 1 2-2h5l2 2h8a2 2 0 0 1 2 2v2M3 18l2-7h17l-2 7a2 2 0 0 1-2 1H5a2 2 0 0 1-2-1Z",
  target:
    "M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0ZM17 12a5 5 0 1 1-10 0 5 5 0 0 1 10 0ZM12 12h.01",
  settings: "M4 6h5m4 0h7M4 12h9m4 0h3M4 18h2m4 0h10M9 4v4m4 2v4m-7 2v4",
  plus: "M12 5v14M5 12h14",
  panel: "M3 4h18v16H3zM9 4v16",
  chart: "M3 3v18h18M7 16l4-4 4 2 5-7",
  moon: "M20.9 13.35A9 9 0 0 1 10.65 3.1 9 9 0 1 0 20.9 13.35Z",
  sun: "M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1",
  chevron: "m6 9 6 6 6-6",
  model: "M7 7h10v10H7zM9 2v5m6-5v5m-6 10v5m6-5v5M2 9h5m-5 6h5m10-6h5m-5 6h5",
  user: "M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM4 21v-2a4 4 0 0 1 4-4h8a4 4 0 0 1 4 4v2",
  memory:
    "M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5M8 12h5m-5 4h8m-5-9 5-5 4 4-5 5z",
} as const;

export function InterviewIcon({ name, size = 18 }: { name: keyof typeof paths; size?: number }) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.65"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={paths[name]} />
    </svg>
  );
}
