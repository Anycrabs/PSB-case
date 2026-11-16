// Фоновая иллюстрация: весы правосудия перед портиком суда. Нарисована линиями, без растровых картинок.
// На начальном экране видна ярче, в диалоге приглушается (класс .is-start у .app).
// Рисунок намеренно простой: четыре колонны по сторонам от весов, без каннелюр и украшений —
// за палитрой вопросов он должен читаться как фон, а не как вторая картинка.

const COLUMNS = [120, 232, 568, 680]

function Pan({ cx, className }: { cx: number; className: string }) {
  return (
    <g className={className}>
      <path d={`M${cx} 330 L${cx - 78} 608 M${cx} 330 L${cx + 78} 608`} />
      <ellipse cx={cx} cy={608} rx={92} ry={11} />
      <path d={`M${cx - 92} 608 Q${cx} 710 ${cx + 92} 608`} />
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
          <stop offset="0" stopColor="#d4ad62" stopOpacity={0.2} />
          <stop offset="1" stopColor="#d4ad62" stopOpacity={0} />
        </radialGradient>
      </defs>
      <circle cx={400} cy={470} r={360} fill="url(#backdrop-glow)" />
      <g className="colonnade" strokeWidth={1.1}>
        <path d="M60 230 L400 82 L740 230 Z" />
        <path opacity={0.6} d="M118 216 L400 104 L682 216 Z" />
        <rect x={48} y={230} width={704} height={34} rx={2} />
        {COLUMNS.map((cx) => (
          <g key={cx}>
            <rect x={cx - 34} y={264} width={68} height={13} rx={2} />
            <path d={`M${cx - 23} 277 L${cx - 25} 806 M${cx + 23} 277 L${cx + 25} 806`} />
            <rect x={cx - 32} y={806} width={64} height={14} rx={2} />
          </g>
        ))}
        <path d="M30 820 H770 M12 838 H788 M-6 856 H806" />
      </g>
      <g className="scales" strokeWidth={2}>
        <circle cx={400} cy={470} r={318} opacity={0.22} />
        <circle cx={400} cy={470} r={334} opacity={0.3} strokeDasharray="1 9" />
        <rect x={296} y={904} width={208} height={26} rx={7} />
        <rect x={338} y={882} width={124} height={22} rx={6} />
        <path d="M392 882 L392 352 M408 882 L408 352" />
        <path d="M400 352 L400 324" />
        <circle cx={400} cy={286} r={11} />
        <g className="beam">
          <path d="M194 323 Q400 302 606 323" />
          <circle cx={188} cy={324} r={6} />
          <circle cx={612} cy={324} r={6} />
          <circle cx={400} cy={314} r={10} />
        </g>
        <Pan cx={188} className="pan-l" />
        <Pan cx={612} className="pan-r" />
      </g>
    </svg>
  )
}
