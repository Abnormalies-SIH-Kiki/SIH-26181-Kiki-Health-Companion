'use client';

import React, { useState } from 'react';

export interface SleepDayRecord {
  day: string;
  date?: string;
  durationMinutes: number; // in minutes (e.g. 450 = 7.5 hrs)
  durationFormatted: string; // e.g. '7h 30m'
  score: number; // 0 - 100
  isPeak?: boolean;
}

export interface SleepTrendsProps {
  /** Initial or model-fed records for 7 days */
  records7D?: SleepDayRecord[];
  /** Initial or model-fed records for 14 days */
  records14D?: SleepDayRecord[];
  /** Average duration formatted string (e.g. '7h 38m') */
  averageDuration?: string;
  /** Best night summary metadata */
  bestNight?: {
    day: string;
    duration: string;
    score: number;
  };
  /** Whether the sensor or ML model is actively syncing */
  isSyncing?: boolean;
  /** Optional container title override */
  title?: string;
}

const DEFAULT_7D_RECORDS: SleepDayRecord[] = [
  { day: 'Mon', durationMinutes: 440, durationFormatted: '7h 20m', score: 78 },
  { day: 'Tue', durationMinutes: 490, durationFormatted: '8h 10m', score: 86 },
  { day: 'Wed', durationMinutes: 390, durationFormatted: '6h 30m', score: 68 },
  { day: 'Thu', durationMinutes: 486, durationFormatted: '8h 06m', score: 94, isPeak: true },
  { day: 'Fri', durationMinutes: 430, durationFormatted: '7h 10m', score: 82 },
  { day: 'Sat', durationMinutes: 510, durationFormatted: '8h 30m', score: 89 },
  { day: 'Sun', durationMinutes: 450, durationFormatted: '7h 30m', score: 80 },
];

const DEFAULT_14D_RECORDS: SleepDayRecord[] = [
  { day: 'W1', durationMinutes: 460, durationFormatted: '7h 40m', score: 82 },
  { day: 'W2', durationMinutes: 475, durationFormatted: '7h 55m', score: 85, isPeak: true },
];

export default function SleepTrendsCard({
  records7D = DEFAULT_7D_RECORDS,
  records14D,
  averageDuration = '7h 38m',
  bestNight = { day: 'Thu', duration: '8h 06m', score: 94 },
  isSyncing = false,
  title = 'Sleep Trends',
}: SleepTrendsProps) {
  const [activeTab, setActiveTab] = useState<'7D' | '14D'>('7D');

  const currentRecords = activeTab === '7D' ? records7D : (records14D || records7D);

  // Calculate SVG curve and bar positions dynamically for 7 days
  const chartWidth = 320;
  const chartHeight = 120;
  const bottomY = 94;
  const topY = 16;

  // Fixed coordinates for 7 slots
  const xPositions = [23, 68, 113, 158, 203, 248, 293];

  // Helper to map score (0-100) to Y coord (lower Y = higher score)
  const getYForScore = (score: number) => {
    const clamped = Math.max(40, Math.min(100, score));
    // 100 -> topY (16), 40 -> bottomY (94)
    return bottomY - ((clamped - 40) / 60) * (bottomY - topY);
  };

  // Helper to map duration in minutes to Bar height (max 600m = 10h)
  const getBarHeight = (mins: number) => {
    const clamped = Math.max(180, Math.min(600, mins));
    return ((clamped - 180) / 420) * 60 + 20; // 20px to 80px
  };

  // Build SVG Path points
  const points = currentRecords.slice(0, 7).map((rec, idx) => {
    const x = xPositions[idx] || 23 + idx * 45;
    const y = getYForScore(rec.score);
    return { x, y, rec };
  });

  // Construct smooth SVG Bezier path
  let pathD = '';
  if (points.length > 0) {
    pathD = `M ${points[0].x} ${points[0].y}`;
    for (let i = 0; i < points.length - 1; i++) {
      const p0 = points[i];
      const p1 = points[i + 1];
      const cp1x = p0.x + (p1.x - p0.x) / 2;
      const cp1y = p0.y;
      const cp2x = p0.x + (p1.x - p0.x) / 2;
      const cp2y = p1.y;
      pathD += ` C ${cp1x} ${cp1y}, ${cp2x} ${cp2y}, ${p1.x} ${p1.y}`;
    }
  }

  const areaD = points.length > 0
    ? `${pathD} L ${points[points.length - 1].x} ${bottomY} L ${points[0].x} ${bottomY} Z`
    : '';

  const peakPoint = points.find((p) => p.rec.isPeak) || points[3];

  return (
    <div className="w-full">
      {/* Section Header with Segmented Switcher */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h3 className="text-lg font-bold text-white tracking-tight leading-tight">{title}</h3>
          {isSyncing && (
            <span className="flex items-center gap-1 text-[10px] text-purple-400 bg-purple-500/10 border border-purple-500/20 px-2 py-0.5 rounded-full uppercase tracking-wider font-semibold">
              <span className="w-1.5 h-1.5 rounded-full bg-purple-400 animate-pulse"></span>
              Live
            </span>
          )}
        </div>
        <div className="inline-flex p-0.5 rounded-lg bg-zinc-900 border border-zinc-800/80 text-xs">
          <button
            type="button"
            onClick={() => setActiveTab('7D')}
            className={`px-3 py-1 rounded-md font-semibold transition-all shadow-sm ${
              activeTab === '7D'
                ? 'bg-purple-700 text-white'
                : 'text-zinc-400 hover:text-white'
            }`}
          >
            7 Days
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('14D')}
            className={`px-3 py-1 rounded-md font-medium transition-all ${
              activeTab === '14D'
                ? 'bg-purple-700 text-white font-semibold shadow-sm'
                : 'text-zinc-400 hover:text-white'
            }`}
          >
            14 Days
          </button>
        </div>
      </div>

      {/* Chart Card */}
      <div className="bg-zinc-900/80 border border-zinc-800/80 rounded-2xl p-4 sm:p-5 shadow-lg flex flex-col gap-3.5 backdrop-blur-md">
        {/* Legend & Average Row */}
        <div className="flex items-center justify-between text-xs">
          <div className="flex items-center gap-3.5">
            <div className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full bg-[#9333EA]"></span>
              <span className="text-zinc-400 font-medium">Score</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-sm bg-zinc-700"></span>
              <span className="text-zinc-400 font-medium">Duration</span>
            </div>
          </div>
          <span className="px-2.5 py-0.5 rounded-full bg-purple-900/30 border border-purple-700/40 text-purple-300 font-medium text-[11px]">
            Avg {averageDuration}
          </span>
        </div>

        {/* Clean SVG Bar/Line Hybrid Chart */}
        <div className="w-full h-36 relative">
          <svg className="w-full h-full" fill="none" preserveAspectRatio="none" viewBox="0 0 320 120">
            <defs>
              <linearGradient id="sleepAreaGradient" x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stopColor="#9333EA" stopOpacity="0.38"></stop>
                <stop offset="100%" stopColor="#9333EA" stopOpacity="0.0"></stop>
              </linearGradient>
            </defs>

            {/* Grid Lines */}
            <line stroke="#27272A" strokeDasharray="3 3" strokeWidth="1" x1="0" x2="320" y1="20" y2="20"></line>
            <line stroke="#27272A" strokeDasharray="3 3" strokeWidth="1" x1="0" x2="320" y1="55" y2="55"></line>
            <line stroke="#27272A" strokeDasharray="3 3" strokeWidth="1" x1="0" x2="320" y1="90" y2="90"></line>

            {/* Duration Bars */}
            {points.map((p, idx) => {
              const h = getBarHeight(p.rec.durationMinutes);
              const y = bottomY - h;
              const isPeak = p.rec.isPeak;
              return (
                <rect
                  key={idx}
                  fill={isPeak ? '#4A1985' : '#27272A'}
                  height={h}
                  rx="3"
                  width="10"
                  x={p.x - 5}
                  y={y}
                  className="transition-all duration-300"
                />
              );
            })}

            {/* Score Area Gradient Fill */}
            {areaD && <path d={areaD} fill="url(#sleepAreaGradient)" />}

            {/* Score Smooth Curve Line */}
            {pathD && <path d={pathD} stroke="#9333EA" strokeLinecap="round" strokeWidth="2.5" />}

            {/* Peak Day Marker */}
            {peakPoint && (
              <>
                <line
                  stroke="#9333EA"
                  strokeDasharray="2 2"
                  strokeWidth="1.5"
                  x1={peakPoint.x}
                  x2={peakPoint.x}
                  y1={peakPoint.y + 5}
                  y2={bottomY}
                ></line>
                <circle
                  cx={peakPoint.x}
                  cy={peakPoint.y}
                  fill="#0A0A0C"
                  r="4.5"
                  stroke="#D8B4FE"
                  strokeWidth="2.5"
                ></circle>
              </>
            )}
          </svg>

          {/* Day Labels Bottom Row */}
          <div className="flex justify-between text-[11px] font-medium text-zinc-400 px-2 mt-1">
            {currentRecords.slice(0, 7).map((rec, idx) => (
              <span
                key={idx}
                className={rec.isPeak ? 'text-purple-300 font-bold' : ''}
              >
                {rec.day}
              </span>
            ))}
          </div>
        </div>

        {/* Highlighted Best Night Callout */}
        <div className="flex items-center gap-2.5 px-3.5 py-2.5 rounded-xl bg-purple-950/40 border border-purple-800/40 mt-1">
          <span className="material-symbols-outlined text-purple-400 text-[18px]">verified</span>
          <div className="flex-1 text-xs">
            <span className="text-zinc-300">Best night: </span>
            <span className="text-white font-semibold">
              {bestNight.day} · {bestNight.duration} · Sleep score {bestNight.score}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
