import { ArrowDownRightIcon, ArrowUpRightIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardHeader, CardPanel, CardTitle } from "@/components/ui/card";

interface Stat {
  label: string;
  value: string;
  delta: number;
  series: number[];
}

const STATS: Stat[] = [
  {
    label: "MRR",
    value: "$48.2k",
    delta: 12.4,
    series: [22, 28, 26, 31, 38, 42, 48],
  },
  {
    label: "Active users",
    value: "12,840",
    delta: 4.1,
    series: [10, 11, 11, 12, 12, 13, 13],
  },
  {
    label: "Churn",
    value: "1.8%",
    delta: -0.6,
    series: [3, 2.8, 2.6, 2.4, 2.1, 2, 1.8],
  },
  {
    label: "NPS",
    value: "62",
    delta: 8.0,
    series: [48, 52, 55, 56, 58, 60, 62],
  },
];

export function CardStatTileShowcasePage() {
  return (
    <div className="min-h-svh bg-background px-6 py-12">
      <div className="mx-auto max-w-5xl">
        <div className="mb-6 flex items-end justify-between">
          <div>
            <h1 className="font-heading text-xl">Overview</h1>
            <p className="text-muted-foreground text-sm">
              Last 30 days vs the previous period.
            </p>
          </div>
          <Badge variant="outline" className="font-mono text-[10px]">
            Apr 2026
          </Badge>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {STATS.map((s) => (
            <StatTile key={s.label} stat={s} />
          ))}
        </div>
      </div>
    </div>
  );
}

function StatTile({ stat }: { stat: Stat }) {
  const positive = stat.delta >= 0;
  return (
    <Card>
      <CardHeader>
        <div className="font-mono text-[10px] text-muted-foreground uppercase tracking-[0.2em]">
          {stat.label}
        </div>
        <CardTitle className="mt-1 font-heading text-2xl tracking-tight">
          {stat.value}
        </CardTitle>
      </CardHeader>
      <CardPanel className="pt-0">
        <Sparkline values={stat.series} positive={positive} />
        <div
          className={
            "mt-3 inline-flex items-center gap-1 font-mono text-xs " +
            (positive ? "text-emerald-600" : "text-destructive")
          }
        >
          {positive ? (
            <ArrowUpRightIcon className="size-3" />
          ) : (
            <ArrowDownRightIcon className="size-3" />
          )}
          {positive ? "+" : ""}
          {stat.delta.toFixed(1)}%
          <span className="text-muted-foreground">vs prev</span>
        </div>
      </CardPanel>
    </Card>
  );
}

function Sparkline({
  values,
  positive,
}: {
  values: number[];
  positive: boolean;
}) {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const w = 100;
  const h = 32;
  const step = w / (values.length - 1);
  const points = values
    .map((v, i) => {
      const x = i * step;
      const y = h - ((v - min) / range) * h;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
  const stroke = positive ? "rgb(5 150 105)" : "var(--destructive)";
  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      className="h-9 w-full"
      aria-hidden
    >
      <polyline
        fill="none"
        stroke={stroke}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        points={points}
      />
    </svg>
  );
}
