"use client";

import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts";

import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
const config = {
  closed: { label: "Closed volume", color: "var(--chart-1)" },
  pipeline: { label: "Open pipeline", color: "var(--chart-2)" },
} satisfies ChartConfig;

export interface RevenuePoint {
  month: string;
  closed: number;
  pipeline: number;
}

/** Data is supplied by the server component that renders this chart. */
export function RevenueChart({
  data,
  className,
}: {
  data: RevenuePoint[];
  className?: string;
}) {
  return (
    <ChartContainer config={config} className={className}>
      <AreaChart data={data} margin={{ left: -12, right: 8, top: 8 }}>
        <defs>
          {Object.entries(config).map(([key, item]) => (
            <linearGradient key={key} id={`fill-${key}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={item.color} stopOpacity={0.32} />
              <stop offset="95%" stopColor={item.color} stopOpacity={0.02} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid vertical={false} strokeDasharray="3 3" />
        <XAxis
          dataKey="month"
          tickLine={false}
          axisLine={false}
          tickMargin={10}
          fontSize={12}
        />
        <YAxis
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          fontSize={12}
          tickFormatter={(value) => `$${value}M`}
        />
        <ChartTooltip
          content={
            <ChartTooltipContent
              formatter={(value, name) => (
                <span className="flex w-full justify-between gap-4">
                  <span className="text-muted-foreground">
                    {config[name as keyof typeof config]?.label}
                  </span>
                  <span className="tabular font-medium">${value}M</span>
                </span>
              )}
            />
          }
        />
        <Area
          dataKey="pipeline"
          type="monotone"
          stroke="var(--color-pipeline)"
          fill="url(#fill-pipeline)"
          strokeWidth={2}
        />
        <Area
          dataKey="closed"
          type="monotone"
          stroke="var(--color-closed)"
          fill="url(#fill-closed)"
          strokeWidth={2}
        />
        <ChartLegend content={<ChartLegendContent />} />
      </AreaChart>
    </ChartContainer>
  );
}
