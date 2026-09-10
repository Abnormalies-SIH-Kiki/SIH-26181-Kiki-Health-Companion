'use client';

import React, { useState } from 'react';

type ViewMode = 'graph' | 'summary';
type TimeRange = 'TODAY' | '7D' | '14D' | '30D';

interface HeartRateLog {
  time: string;
  bpm: number;
  category: string;
  tag: string;
  tagColor: string;
  tagBg: string;
}

const todayLogs: HeartRateLog[] = [
  { time: '08:15 PM', bpm: 74, category: 'Resting Rhythm', tag: 'RESTING', tagColor: 'text-blue-400', tagBg: 'bg-blue-500/10 border-blue-500/20' },
  { time: '06:30 PM', bpm: 118, category: 'Evening Cardio', tag: 'PEAK', tagColor: 'text-rose-400', tagBg: 'bg-rose-500/10 border-rose-500/20' },
  { time: '03:45 PM', bpm: 82, category: 'Active Walk / Routine', tag: 'ACTIVE', tagColor: 'text-emerald-400', tagBg: 'bg-emerald-500/10 border-emerald-500/20' },
  { time: '12:20 PM', bpm: 71, category: 'Post-Lunch Rest', tag: 'NORMAL', tagColor: 'text-blue-400', tagBg: 'bg-blue-500/10 border-blue-500/20' },
  { time: '09:10 AM', bpm: 88, category: 'Morning Commute', tag: 'ACTIVE', tagColor: 'text-emerald-400', tagBg: 'bg-emerald-500/10 border-emerald-500/20' },
  { time: '06:00 AM', bpm: 58, category: 'Deep Sleep Baseline', tag: 'OPTIMAL', tagColor: 'text-purple-400', tagBg: 'bg-purple-500/10 border-purple-500/20' },
];

const weekLogs: HeartRateLog[] = [
  { time: 'Today', bpm: 74, category: 'Avg • Range 58 - 118', tag: 'NORMAL', tagColor: 'text-emerald-400', tagBg: 'bg-emerald-500/10 border-emerald-500/20' },
  { time: 'Yesterday', bpm: 70, category: 'Avg • Range 56 - 122', tag: 'RESTING', tagColor: 'text-blue-400', tagBg: 'bg-blue-500/10 border-blue-500/20' },
  { time: 'Thu, Sep 4', bpm: 83, category: 'Avg • Range 62 - 138', tag: 'CARDIO', tagColor: 'text-amber-400', tagBg: 'bg-amber-500/10 border-amber-500/20' },
  { time: 'Wed, Sep 3', bpm: 68, category: 'Avg • Range 55 - 110', tag: 'OPTIMAL', tagColor: 'text-purple-400', tagBg: 'bg-purple-500/10 border-purple-500/20' },
  { time: 'Tue, Sep 2', bpm: 72, category: 'Avg • Range 59 - 115', tag: 'NORMAL', tagColor: 'text-emerald-400', tagBg: 'bg-emerald-500/10 border-emerald-500/20' },
  { time: 'Mon, Sep 1', bpm: 76, category: 'Avg • Range 58 - 126', tag: 'ACTIVE', tagColor: 'text-emerald-400', tagBg: 'bg-emerald-500/10 border-emerald-500/20' },
];

export default function HeartRateTrends() {
  const [viewMode, setViewMode] = useState<ViewMode>('graph');
  const [timeRange, setTimeRange] = useState<TimeRange>('TODAY');

  const logs = timeRange === 'TODAY' ? todayLogs : weekLogs;

  return (
    <div className="bg-[#1C1C1E] metric-group-glow rounded-xl p-6 mb-4 border border-[rgba(255,255,255,0.05)] transition-all">
      {/* Header with Switcher and Filter Controls */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-5">
        <div className="flex items-center justify-between w-full sm:w-auto gap-3">
          {/* Graph vs Summary Toggle */}
          <div className="flex items-center gap-1 p-1 bg-[#121212] rounded-full border border-[rgba(255,255,255,0.05)]">
            <button
              onClick={() => setViewMode('graph')}
              className={`text-[12px] px-4 py-1.5 rounded-full transition-all duration-200 whitespace-nowrap ${
                viewMode === 'graph'
                  ? 'font-semibold bg-[#2A2A2A] text-white shadow-sm'
                  : 'font-medium text-neutral-400 hover:text-white'
              }`}
            >
              Graph
            </button>
            <button
              onClick={() => setViewMode('summary')}
              className={`text-[12px] px-4 py-1.5 rounded-full transition-all duration-200 whitespace-nowrap flex items-center gap-1.5 ${
                viewMode === 'summary'
                  ? 'font-semibold bg-[#2A2A2A] text-white shadow-sm'
                  : 'font-medium text-neutral-400 hover:text-white'
              }`}
            >
              Summary
              <span className="w-1.5 h-1.5 rounded-full bg-rose-500 animate-pulse"></span>
            </button>
          </div>
          <span className="text-[12px] font-semibold tracking-wider text-neutral-400 uppercase whitespace-nowrap hidden sm:inline">
            HEART RATE HISTORY
          </span>
        </div>

        {/* Time range pill buttons */}
        <div className="flex justify-end w-full sm:w-auto">
          <div className="flex items-center gap-1.5 bg-[#121212] p-1 rounded-full border border-[rgba(255,255,255,0.05)]">
            {(['TODAY', '7D', '14D', '30D'] as TimeRange[]).map((range) => (
              <button
                key={range}
                onClick={() => setTimeRange(range)}
                className={`text-[11px] px-3 py-1 rounded-full transition-all whitespace-nowrap ${
                  timeRange === range
                    ? 'font-bold bg-[#2A2A2A] text-white shadow-sm'
                    : 'font-medium text-neutral-400 hover:text-white'
                }`}
              >
                {range}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Main Viewport: Graph OR Summary List */}
      {viewMode === 'graph' ? (
        <div className="h-48 w-full relative mb-4">
          <div className="absolute left-0 h-full flex flex-col justify-between py-2 text-[12px] font-medium text-neutral-400 opacity-60">
            <span className="leading-none">120</span>
            <span className="leading-none">90</span>
            <span className="leading-none">60</span>
          </div>
          <div className="ml-8 h-full relative border-b border-[rgba(255,255,255,0.05)]">
            <svg className="w-full h-full" preserveAspectRatio="none" viewBox="0 0 300 150">
              <defs>
                <linearGradient id="chartGlow" x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0%" stopColor="#EF4444" stopOpacity="0.25"></stop>
                  <stop offset="100%" stopColor="#EF4444" stopOpacity="0"></stop>
                </linearGradient>
              </defs>
              <path
                d="M0 100 C 50 100, 70 60, 100 80 C 130 100, 150 40, 180 50 C 210 60, 230 110, 260 90 C 280 80, 290 60, 300 70 L 300 150 L 0 150 Z"
                fill="url(#chartGlow)"
              ></path>
              <path
                d="M0 100 C 50 100, 70 60, 100 80 C 130 100, 150 40, 180 50 C 210 60, 230 110, 260 90 C 280 80, 290 60, 300 70"
                fill="none"
                stroke="#EF4444"
                strokeLinecap="round"
                strokeWidth="2.5"
              ></path>
            </svg>
          </div>
          <div className="ml-8 mt-2 flex justify-between text-[12px] font-medium text-neutral-400 opacity-60">
            <span>12 AM</span>
            <span>6 AM</span>
            <span>12 PM</span>
            <span>6 PM</span>
            <span>NOW</span>
          </div>
        </div>
      ) : (
        /* Summary View: Rich List of Timings & Heart Rate */
        <div className="h-48 overflow-y-auto pr-1 space-y-2 mb-4 scrollbar-none">
          <div className="flex items-center justify-between px-2 pb-1 text-[11px] font-semibold text-neutral-500 uppercase tracking-wider border-b border-white/[0.04]">
            <span>Time & Context</span>
            <span>Heart Rate</span>
          </div>
          {logs.map((item, index) => (
            <div
              key={index}
              className="bg-[#141416]/80 border border-white/[0.06] hover:border-white/[0.14] rounded-xl p-2.5 px-3 flex items-center justify-between transition-all duration-200 group"
            >
              {/* Left Side: Icon & Timing Details */}
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-rose-500/10 border border-rose-500/20 flex items-center justify-center text-rose-400 flex-shrink-0 group-hover:scale-105 transition-transform">
                  <span className="material-symbols-outlined text-[16px]" style={{ fontVariationSettings: "'FILL' 1" }}>
                    favorite
                  </span>
                </div>
                <div className="flex flex-col">
                  <div className="flex items-center gap-2">
                    <span className="text-[13px] font-semibold text-white tracking-tight">{item.time}</span>
                    <span
                      className={`text-[9px] font-bold px-1.5 py-0.5 rounded border uppercase tracking-wider ${item.tagBg} ${item.tagColor}`}
                    >
                      {item.tag}
                    </span>
                  </div>
                  <span className="text-[11px] text-neutral-400 font-normal leading-tight">{item.category}</span>
                </div>
              </div>

              {/* Right Side: BPM reading */}
              <div className="flex items-baseline gap-1">
                <span className="text-base font-bold text-white tracking-tight group-hover:text-rose-400 transition-colors">
                  {item.bpm}
                </span>
                <span className="text-[11px] font-medium text-neutral-400">BPM</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Aggregate Metric Stats Footer */}
      <div className="pt-4 mt-2 border-t border-[rgba(255,255,255,0.05)] grid grid-cols-2 sm:grid-cols-4 gap-4">
        <div className="flex flex-col">
          <span className="text-[11px] font-semibold tracking-wider text-neutral-400 mb-1 uppercase">RESTING</span>
          <div className="flex items-baseline gap-1.5 whitespace-nowrap">
            <span className="text-2xl font-bold text-white leading-tight">58</span>
            <span className="text-[12px] font-medium text-neutral-400">bpm</span>
          </div>
        </div>
        <div className="flex flex-col">
          <span className="text-[11px] font-semibold tracking-wider text-neutral-400 mb-1 uppercase">AVERAGE</span>
          <div className="flex items-baseline gap-1.5 whitespace-nowrap">
            <span className="text-2xl font-bold text-white leading-tight">74</span>
            <span className="text-[12px] font-medium text-neutral-400">bpm</span>
          </div>
        </div>
        <div className="flex flex-col">
          <span className="text-[11px] font-semibold tracking-wider text-neutral-400 mb-1 uppercase">MINIMUM</span>
          <div className="flex items-baseline gap-1.5 whitespace-nowrap">
            <span className="text-2xl font-bold text-white leading-tight">56</span>
            <span className="text-[12px] font-medium text-neutral-400">bpm</span>
          </div>
        </div>
        <div className="flex flex-col">
          <span className="text-[11px] font-semibold tracking-wider text-rose-500 mb-1 uppercase">MAXIMUM</span>
          <div className="flex items-baseline gap-1.5 whitespace-nowrap">
            <span className="text-2xl font-bold text-rose-500 leading-tight">118</span>
            <span className="text-[12px] font-medium text-rose-500/80">bpm</span>
          </div>
        </div>
      </div>
    </div>
  );
}
