"use client";

import { useEffect, useRef } from "react";
import {
  ColorType,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";

import { toChartNumber, type DecimalString } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * Chart values are JS numbers because that is what a canvas needs. This is the one place the
 * decimal-string rule is relaxed, and it is confined to pixel positions — every number a user
 * *reads* still comes from the API's formatted decimal.
 */
function toSeconds(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

/** Read the live theme tokens so charts re-colour with the rest of the product. */
function readTheme() {
  if (typeof window === "undefined") {
    return { ink: "#181b1a", faint: "#8c928f", line: "#e2e2e0", accent: "#115e4f", loss: "#b22d3a", surface: "#ffffff" };
  }
  const styles = getComputedStyle(document.documentElement);
  const token = (name: string, fallback: string) => {
    const value = styles.getPropertyValue(name).trim();
    return value ? `rgb(${value.split(" ").join(",")})` : fallback;
  };
  return {
    ink: token("--ink", "#181b1a"),
    faint: token("--faint", "#8c928f"),
    line: token("--line", "#e2e2e0"),
    accent: token("--accent", "#115e4f"),
    loss: token("--loss", "#b22d3a"),
    profit: token("--profit", "#0d7a5a"),
    surface: token("--surface", "#ffffff"),
  };
}

export interface SeriesPoint {
  timestamp: string;
  value: DecimalString;
}

/**
 * Area/line chart for equity and drawdown series.
 *
 * Renders an explicit empty state rather than an axis with no data — an empty chart frame reads
 * as "zero", which is a different claim from "nothing to show yet".
 */
export function SeriesChart({
  points,
  height = 260,
  tone = "accent",
  baseline,
  className,
  emptyMessage = "No data for this range yet.",
}: {
  points: SeriesPoint[];
  height?: number;
  tone?: "accent" | "loss";
  /** Draw a reference line, e.g. starting capital on an equity curve. */
  baseline?: DecimalString;
  className?: string;
  emptyMessage?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || points.length === 0) return;

    const theme = readTheme();
    const colour = tone === "loss" ? theme.loss : theme.accent;

    const chart = createChart(container, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: theme.faint,
        fontSize: 11,
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { color: theme.line, style: 1 },
      },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.15, bottom: 0.1 } },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
      crosshair: { mode: 1, vertLine: { labelBackgroundColor: colour }, horzLine: { labelBackgroundColor: colour } },
      handleScale: { axisPressedMouseMove: false },
    });

    const series = chart.addAreaSeries({
      lineColor: colour,
      topColor: `${colour.replace("rgb(", "rgba(").replace(")", "")},0.22)`,
      bottomColor: `${colour.replace("rgb(", "rgba(").replace(")", "")},0.01)`,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    });

    // De-duplicate and sort: lightweight-charts requires strictly ascending unique times.
    const byTime = new Map<number, number>();
    for (const point of points) {
      byTime.set(toSeconds(point.timestamp), toChartNumber(point.value));
    }
    series.setData(
      [...byTime.entries()]
        .sort((a, b) => a[0] - b[0])
        .map(([time, value]) => ({ time: time as UTCTimestamp, value })),
    );

    if (baseline !== undefined && baseline !== null) {
      series.createPriceLine({
        price: toChartNumber(baseline),
        color: theme.faint,
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: false,
        title: "start",
      });
    }

    chart.timeScale().fitContent();
    chartRef.current = chart;
    seriesRef.current = series;

    const observer = new ResizeObserver(([entry]) => {
      if (entry) chart.applyOptions({ width: entry.contentRect.width });
    });
    observer.observe(container);

    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [points, height, tone, baseline]);

  if (points.length === 0) {
    return (
      <div
        className={cn("flex items-center justify-center rounded border border-dashed border-line text-xs text-faint", className)}
        style={{ height }}
      >
        {emptyMessage}
      </div>
    );
  }

  return <div ref={containerRef} className={cn("w-full", className)} style={{ height }} />;
}
