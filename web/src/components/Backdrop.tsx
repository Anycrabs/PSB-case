// Фоновая иллюстрация: весы правосудия перед колоннадой суда. Нарисована линиями, без растровых картинок.
// На начальном экране видна ярче, в диалоге приглушается (класс .is-start у .app).

const COLUMNS = [120, 232, 344, 456, 568, 680]

function Pan({ cx, className }: { cx: number; className: string }) {
  return (
    <g className={className}>
      <path d={`M${cx} 330 L${cx - 78} 602 M${cx} 330 L${cx + 78} 602`} />
      <path opacity={0.55} d={`M${cx} 330 L${cx} 596`} />
      <ellipse cx={cx} cy={602} rx={92} ry={11} />
      <path d={`M${cx - 92} 602 Q${cx} 704 ${cx + 92} 602`} />
      <path opacity={0.5} d={`M${cx - 64} 626 Q${cx} 676 ${cx + 64} 626`} />
    </g>
  )
}

export function Backdrop() {
  return (
    <svg
      className="backdrop"
      viewBox="0 0 800 1000"
      aria-hidden="true"
      focusable="false"
      fill="none"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <defs>
        <radialGradient id="backdrop-glow">
          <stop offset="0" stopColor="#d4ad62" stopOpacity={0.22} />
          <stop offset="1" stopColor="#d4ad62" stopOpacity={0} />
        </radialGradient>
      </defs>
      <circle cx={400} cy={470} r={360} fill="url(#backdrop-glow)" />
      <g className="colonnade" strokeWidth={1.1}>
        <path d="M60 230 L400 82 L740 230 Z" />
        <path opacity={0.6} d="M118 216 L400 104 L682 216 Z" />
        <rect x={48} y={230} width={704} height={34} rx={2} />
        <path opacity={0.6} d="M48 247 H752" />
        {COLUMNS.map((cx) => (
          <g key={cx}>
            <rect x={cx - 34} y={264} width={68} height={12} rx={2} />
            <rect x={cx - 29} y={276} width={58} height={8} rx={2} />
            <path d={`M${cx - 23} 284 L${cx - 26} 806 M${cx + 23} 284 L${cx + 26} 806`} />
            <path opacity={0.5} d={`M${cx - 11} 290 L${cx - 12} 800 M${cx} 290 L${cx} 800 M${cx + 11} 290 L${cx + 12} 800`} />
            <rect x={cx - 32} y={806} width={64} height={14} rx={2} />
          </g>
        ))}
        <rect x={30} y={820} width={740} height={18} rx={2} />
        <rect x={12} y={838} width={776} height={18} rx={2} />
        <rect x={-6} y={856} width={812} height={18} rx={2} />
      </g>
      <g className="scales" strokeWidth={2}>
        <circle cx={400} cy={470} r={318} opacity={0.22} />
        <circle cx={400} cy={470} r={334} opacity={0.3} strokeDasharray="1 9" />
        <rect x={296} y={904} width={208} height={26} rx={7} />
        <rect x={338} y={882} width={124} height={22} rx={6} />
        <path d="M391 882 L393 352 M409 882 L407 352" />
        <ellipse cx={400} cy={760} rx={19} ry={6} />
        <ellipse cx={400} cy={600} rx={15} ry={5} />
        <ellipse cx={400} cy={452} rx={13} ry={4.5} />
        <path d="M400 352 L400 296" />
        <circle cx={400} cy={286} r={11} />
        <path d="M400 275 L400 250" />
        <circle cx={400} cy={246} r={4} />
        <g className="beam">
          <path d="M188 322 Q400 296 612 322" />
          <path opacity={0.6} d="M206 330 Q400 308 594 330" />
          <circle cx={188} cy={324} r={6} />
          <circle cx={612} cy={324} r={6} />
          <circle cx={400} cy={314} r={10} />
          <path opacity={0.7} d="M300 318 q20 -16 40 -4 M500 318 q-20 -16 -40 -4" />
        </g>
        <Pan cx={188} className="pan-l" />
        <Pan cx={612} className="pan-r" />
      </g>
    </svg>
  )
}
