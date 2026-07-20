"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  XAxis,
  YAxis,
} from "recharts";

import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
export interface SourcePoint {
  source: string;
  count: number;
}
export interface MixSlice {
  name: string;
  value: number;
}
export interface FunnelStep {
  stage: string;
  value: number;
}

const sourceConfig = {
  count: { label: "Leads", color: "var(--chart-1)" },
} satisfies ChartConfig;

/** All chart data arrives as props from the server component. */
export function LeadSourceChart({
  data,
  className,
}: {
  data: SourcePoint[];
  className?: string;
}) {
  return (
    <ChartContainer config={sourceConfig} className={className}>
      <BarChart
        data={data}
        layout="vertical"
        margin={{ left: 8, right: 16 }}
      >
        <CartesianGrid horizontal={false} strokeDasharray="3 3" />
        <XAxis type="number" hide />
        <YAxis
          type="category"
          dataKey="source"
          tickLine={false}
          axisLine={false}
          width={88}
          fontSize={12}
        />
        <ChartTooltip content={<ChartTooltipContent />} />
        <Bar dataKey="count" fill="var(--color-count)" radius={[0, 6, 6, 0]} barSize={20} />
      </BarChart>
    </ChartContainer>
  );
}

export function DealMixChart({
  data,
  className,
}: {
  data: MixSlice[];
  className?: string;
}) {
  const mixConfig = data.reduce<ChartConfig>((config, slice, index) => {
    config[slice.name] = {
      label: slice.name,
      color: `var(--chart-${index + 1})`,
    };
    return config;
  }, {});

  return (
    <ChartContainer config={mixConfig} className={className}>
      <PieChart>
        <ChartTooltip content={<ChartTooltipContent nameKey="name" />} />
        <Pie
          data={data}
          dataKey="value"
          nameKey="name"
          innerRadius="55%"
          outerRadius="82%"
          paddingAngle={2}
          strokeWidth={0}
        >
          {data.map((slice, index) => (
            <Cell key={slice.name} fill={`var(--chart-${index + 1})`} />
          ))}
        </Pie>
        <ChartLegend content={<ChartLegendContent nameKey="name" />} />
      </PieChart>
    </ChartContainer>
  );
}

/**
 * Rendered as proportional bars rather than a true funnel shape — easier to
 * read the drop-off percentages, which is the point of the panel.
 */
export function ConversionFunnel({
  data,
  className,
}: {
  data: FunnelStep[];
  className?: string;
}) {
  const top = data[0].value;

  return (
    <div className={className}>
      <ol className="space-y-3">
        {data.map((step, index) => {
          const share = (step.value / top) * 100;
          const previous = index > 0 ? data[index - 1].value : null;
          const dropOff = previous
            ? Math.round(((previous - step.value) / previous) * 100)
            : null;

          return (
            <li key={step.stage}>
              <div className="mb-1.5 flex items-baseline justify-between gap-2 text-sm">
                <span className="font-medium">{step.stage}</span>
                <span className="tabular flex items-baseline gap-2">
                  <span className="font-semibold">{step.value}</span>
                  {dropOff !== null && (
                    <span className="text-xs text-destructive">−{dropOff}%</span>
                  )}
                </span>
              </div>
              <div className="h-7 overflow-hidden rounded-md bg-muted">
                <div
                  className="h-full rounded-md"
                  style={{
                    width: `${share}%`,
                    backgroundColor: `var(--chart-${(index % 5) + 1})`,
                    opacity: 1 - index * 0.1,
                  }}
                />
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
