"use client";

import { useEffect, useRef } from "react";
import {
  ColorType,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";

import type { Candle } from "@/lib/types";
import { toChartNumber, type DecimalString } from "@/lib/format";
import { cn } from "@/lib/utils";

function toSeconds(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

function readTheme() {
  if (typeof window === "undefined") {
    return { faint: "#8c928f", line: "#e2e2e0", profit: "#0d7a5a", loss: "#b22d3a", info: "#275994", warn: "#a66c10" };
  }
  const styles = getComputedStyle(document.documentElement);
  const token = (name: string, fallback: string) => {
    const value = styles.getPropertyValue(name).trim();
    return value ? `rgb(${value.split(" ").join(",")})` : fallback;
  };
  return {
    faint: token("--faint", "#8c928f"),
    line: token("--line", "#e2e2e0"),
    profit: token("--profit", "#0d7a5a"),
    loss: token("--loss", "#b22d3a"),
    info: token("--info", "#275994"),
    warn: token("--warn", "#a66c10"),
  };
}

export interface PriceLevel {
  price: DecimalString;
  label: string;
  tone: "entry" | "exit" | "stop" | "target";
}

export interface TradeMarker {
  time: string;
  side: "buy" | "sell";
  label: string;
}

/**
 * Candlestick chart with optional trade context.
 *
 * Used by the trade detail view and by replay. In replay the caller passes only the candles the
 * server has revealed, so the chart physically cannot show a bar the session has not reached.
 */
export function CandleChart({
  candles,
  levels = [],
  markers = [],
  height = 380,
  className,
  emptyMessage = "No market data available for this instrument and period.",
}: {
  candles: Candle[];
  levels?: PriceLevel[];
  markers?: TradeMarker[];
  height?: number;
  className?: string;
  emptyMessage?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || candles.length === 0) return;

    const theme = readTheme();
    const chart = createChart(container, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: theme.faint,
        fontSize: 11,
      },
      grid: { vertLines: { color: theme.line, style: 1 }, horzLines: { color: theme.line, style: 1 } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
      crosshair: { mode: 1 },
    });

    const series = chart.addCandlestickSeries({
      upColor: theme.profit,
      downColor: theme.loss,
      borderUpColor: theme.profit,
      borderDownColor: theme.loss,
      wickUpColor: theme.profit,
      wickDownColor: theme.loss,
    });

    const byTime = new Map<number, Candle>();
    for (const candle of candles) byTime.set(toSeconds(candle.time), candle);

    series.setData(
      [...byTime.entries()]
        .sort((a, b) => a[0] - b[0])
        .map(([time, candle]) => ({
          time: time as UTCTimestamp,
          open: toChartNumber(candle.open),
          high: toChartNumber(candle.high),
          low: toChartNumber(candle.low),
          close: toChartNumber(candle.close),
        })),
    );

    const LEVEL_COLOURS = {
      entry: theme.info,
      exit: theme.faint,
      stop: theme.loss,
      target: theme.profit,
    } as const;

    for (const level of levels) {
      if (level.price === null || level.price === undefined) continue;
      series.createPriceLine({
        price: toChartNumber(level.price),
        color: LEVEL_COLOURS[level.tone],
        lineWidth: 1,
        lineStyle: level.tone === "entry" || level.tone === "exit" ? 0 : 2,
        axisLabelVisible: true,
        title: level.label,
      });
    }

    if (markers.length > 0) {
      const seriesMarkers: SeriesMarker<Time>[] = markers
        .map((marker) => ({
          time: toSeconds(marker.time) as Time,
          position: marker.side === "buy" ? ("belowBar" as const) : ("aboveBar" as const),
          color: marker.side === "buy" ? theme.profit : theme.loss,
          shape: marker.side === "buy" ? ("arrowUp" as const) : ("arrowDown" as const),
          text: marker.label,
        }))
        .sort((a, b) => Number(a.time) - Number(b.time));
      series.setMarkers(seriesMarkers);
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
  }, [candles, levels, markers, height]);

  if (candles.length === 0) {
    return (
      <div
        className={cn("flex items-center justify-center rounded border border-dashed border-line px-4 text-center text-xs text-faint", className)}
        style={{ height }}
      >
        {emptyMessage}
      </div>
    );
  }

  return <div ref={containerRef} className={cn("w-full", className)} style={{ height }} />;
}
