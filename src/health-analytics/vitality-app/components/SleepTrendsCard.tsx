'use client';

import React, { useState } from 'react';

type ViewMode = 'graph' | 'summary';

interface SleepStageLog {
  stage: string;
  duration: string;
  percentage: string;
  timeRange: string;
  tag: string;
  tagColor: string;
  tagBg: string;
  icon: string;
}

const sleepStageLogs: SleepStageLog[] = [
  {
    stage: 'Deep Sleep',
    duration: '1h 45m',
    percentage: '23%',
    timeRange: '12:10 AM – 01:55 AM · Stage 3 & 4',
    tag: 'RESTORATIVE',
    tagColor: 'text-indigo-400',
    tagBg: 'bg-indigo-500/10 border-indigo-500/20',
    icon: 'bedtime',
  },
  {
    stage: 'REM Sleep',
    duration: '1h 52m',
    percentage: '25%',
    timeRange: '01:55 AM – 02:40 AM & 04:30 AM – 05:37 AM',
    tag: 'COGNITIVE',
    tagColor: 'text-purple-400',
    tagBg: 'bg-purple-500/10 border-purple-500/20',
    icon: 'psychology',
  },
  {
    stage: 'Core / Light Sleep',
    duration: '3h 18m',
    percentage: '43%',
    timeRange: '11:22 PM – 12:10 AM & 03:20 AM – 04:30 AM',
    tag: 'MAINTENANCE',
    tagColor: 'text-blue-400',
    tagBg: 'bg-blue-500/10 border-blue-500/20',
    icon: 'nightlight',
  },
  {
    stage: 'Awake Periods',
    duration: '42m',
    percentage: '9%',
    timeRange: '11:08 PM onset + brief micro-awakenings',
    tag: 'NORMAL',
    tagColor: 'text-zinc-400',
    tagBg: 'bg-zinc-700/20 border-zinc-700/30',
    icon: 'wb_twilight',
  },
];

export default function SleepTrendsCard() {
  const [viewMode, setViewMode] = useState<ViewMode>('graph');

  // Curve points representing the 7h 37m logged sleep data (11:08 PM to 06:45 AM)
  const timelinePoints = [
    { x: 20, y: 84, time: '11:08 PM', label: 'Onset' },
    { x: 58, y: 52, time: '12:00 AM', label: 'Light' },
    { x: 102, y: 22, time: '01:15 AM', label: 'Deep' },
    { x: 148, y: 44, time: '02:30 AM', label: 'REM' },
    { x: 192, y: 24, time: '03:45 AM', label: 'Deep' },
    { x: 236, y: 46, time: '05:00 AM', label: 'REM' },
    { x: 276, y: 60, time: '06:15 AM', label: 'Light' },
    { x: 304, y: 84, time: '06:45 AM', label: 'Awake' },
  ];

  // SVG Bezier path
  let pathD = `M ${timelinePoints[0].x} ${timelinePoints[0].y}`;
  for (let i = 0; i < timelinePoints.length - 1; i++) {
    const p0 = timelinePoints[i];
    const p1 = timelinePoints[i + 1];
    const cp1x = p0.x + (p1.x - p0.x) / 2;
    const cp1y = p0.y;
    const cp2x = p0.x + (p1.x - p0.x) / 2;
    const cp2y = p1.y;
    pathD += ` C ${cp1x} ${cp1y}, ${cp2x} ${cp2y}, ${p1.x} ${p1.y}`;
  }

  const bottomY = 100;
  const areaD = `${pathD} L ${timelinePoints[timelinePoints.length - 1].x} ${bottomY} L ${timelinePoints[0].x} ${bottomY} Z`;

  return (
    <div className="w-full">
      {/* Section Header with Graph / Summary Switcher */}
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-xl sm:text-2xl font-bold text-white tracking-tight leading-tight">
          Sleep Trends
        </h2>

        {/* Switcher: Graph vs Summary (Replacing the old 7 Days / 14 Days switcher) */}
        <div className="inline-flex p-1 rounded-xl bg-zinc-900 border border-zinc-800/80 text-xs">
          <button
            type="button"
            onClick={() => setViewMode('graph')}
            className={`px-3.5 py-1.5 rounded-lg font-semibold transition-all duration-200 ${
              viewMode === 'graph'
                ? 'bg-purple-600 text-white shadow-sm'
                : 'text-zinc-400 hover:text-white'
            }`}
          >
            Graph
          </button>
          <button
            type="button"
            onClick={() => setViewMode('summary')}
            className={`px-3.5 py-1.5 rounded-lg font-semibold transition-all duration-200 flex items-center gap-1.5 ${
              viewMode === 'summary'
                ? 'bg-purple-600 text-white shadow-sm'
                : 'text-zinc-400 hover:text-white'
            }`}
          >
            Summary
            <span className="w-1.5 h-1.5 rounded-full bg-purple-300 animate-pulse"></span>
          </button>
        </div>
      </div>

      {/* Main Card Viewport */}
      <div className="bg-zinc-900/80 border border-zinc-800/80 rounded-2xl p-4 sm:p-5 shadow-lg flex flex-col gap-3.5 backdrop-blur-md">
        {viewMode === 'graph' ? (
          /* Graph View: Displays only the 7h 37m logged sleep data */
          <div className="flex flex-col gap-3">
            {/* Legend & Average Row */}
            <div className="flex items-center justify-between text-xs">
              <div className="flex items-center gap-3.5">
                <div className="flex items-center gap-1.5">
                  <span className="w-2.5 h-2.5 rounded-full bg-[#9333EA]"></span>
                  <span className="text-zinc-400 font-medium">Score 84</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="w-2.5 h-2.5 rounded-sm bg-zinc-700"></span>
                  <span className="text-zinc-400 font-medium">Duration 7h 37m</span>
                </div>
              </div>
              <span className="px-2.5 py-0.5 rounded-full bg-purple-900/30 border border-purple-700/40 text-purple-300 font-medium text-[11px]">
                Avg 7h 37m
              </span>
            </div>

            {/* SVG Hypnogram / Sleep Architecture Graph for 7h 37m */}
            <div className="w-full h-36 relative mt-1">
              <svg className="w-full h-full" fill="none" preserveAspectRatio="none" viewBox="0 0 320 110">
                <defs>
                  <linearGradient id="sleepAreaGrad" x1="0" x2="0" y1="0" y2="1">
                    <stop offset="0%" stopColor="#9333EA" stopOpacity="0.4"></stop>
                    <stop offset="100%" stopColor="#9333EA" stopOpacity="0.0"></stop>
                  </linearGradient>
                </defs>

                {/* Horizontal reference lines */}
                <line stroke="#27272A" strokeDasharray="3 3" strokeWidth="1" x1="10" x2="310" y1="22" y2="22"></line>
                <line stroke="#27272A" strokeDasharray="3 3" strokeWidth="1" x1="10" x2="310" y1="52" y2="52"></line>
                <line stroke="#27272A" strokeDasharray="3 3" strokeWidth="1" x1="10" x2="310" y1="84" y2="84"></line>

                {/* Sleep Depth Stage Labels */}
                <text x="14" y="20" fill="#A855F7" fontSize="8" fontWeight="600">DEEP</text>
                <text x="14" y="50" fill="#71717A" fontSize="8" fontWeight="500">REM/LIGHT</text>
                <text x="14" y="82" fill="#71717A" fontSize="8" fontWeight="500">AWAKE</text>

                {/* Area Gradient */}
                <path d={areaD} fill="url(#sleepAreaGrad)" />

                {/* Smooth Hypnogram Curve Line */}
                <path d={pathD} stroke="#A855F7" strokeLinecap="round" strokeWidth="2.5" />

                {/* Deep Sleep Peak Points */}
                <circle cx="102" cy="22" r="4" fill="#0A0A0C" stroke="#C084FC" strokeWidth="2" />
                <circle cx="192" cy="24" r="4" fill="#0A0A0C" stroke="#C084FC" strokeWidth="2" />
              </svg>

              {/* Timeline bottom markers */}
              <div className="flex justify-between text-[10px] font-medium text-zinc-400 px-1 mt-1">
                <span>11:08 PM</span>
                <span>01:15 AM</span>
                <span>03:45 AM</span>
                <span>05:30 AM</span>
                <span>06:45 AM</span>
              </div>
            </div>

            {/* Stage Quick Chips */}
            <div className="grid grid-cols-4 gap-2 pt-2 border-t border-zinc-800/80 mt-1 text-center">
              <div className="bg-zinc-800/40 rounded-xl p-2 border border-zinc-800/60">
                <span className="block text-[10px] text-zinc-400 uppercase tracking-wider">Total</span>
                <span className="text-xs font-bold text-white">7h 37m</span>
              </div>
              <div className="bg-indigo-950/30 rounded-xl p-2 border border-indigo-900/30">
                <span className="block text-[10px] text-indigo-300 uppercase tracking-wider">Deep</span>
                <span className="text-xs font-bold text-indigo-200">1h 45m</span>
              </div>
              <div className="bg-purple-950/30 rounded-xl p-2 border border-purple-900/30">
                <span className="block text-[10px] text-purple-300 uppercase tracking-wider">REM</span>
                <span className="text-xs font-bold text-purple-200">1h 52m</span>
              </div>
              <div className="bg-blue-950/30 rounded-xl p-2 border border-blue-900/30">
                <span className="block text-[10px] text-blue-300 uppercase tracking-wider">Core</span>
                <span className="text-xs font-bold text-blue-200">3h 18m</span>
              </div>
            </div>

            {/* Highlighted Callout */}
            <div className="flex items-center gap-2.5 px-3.5 py-2.5 rounded-xl bg-purple-950/40 border border-purple-800/40 mt-0.5">
              <span className="material-symbols-outlined text-purple-400 text-[18px]">verified</span>
              <div className="flex-1 text-xs">
                <span className="text-zinc-300">Last night: </span>
                <span className="text-white font-semibold">
                  11:08 PM – 06:45 AM · 7h 37m · Sleep score 84
                </span>
              </div>
            </div>
          </div>
        ) : (
          /* Summary View: Displays the logged data in form of list / stacks */
          <div className="flex flex-col gap-2.5">
            <div className="flex items-center justify-between px-1 pb-1 text-[11px] font-semibold text-zinc-400 uppercase tracking-wider border-b border-zinc-800/80">
              <span>Stage & Timeline</span>
              <span>Duration</span>
            </div>

            {/* Main Logged Session Summary Card */}
            <div className="bg-gradient-to-r from-purple-950/40 to-zinc-900 border border-purple-800/40 rounded-xl p-3 flex items-center justify-between shadow-sm">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-purple-500/20 border border-purple-400/30 flex items-center justify-center text-purple-300 flex-shrink-0">
                  <span className="material-symbols-outlined text-[18px]">bedtime</span>
                </div>
                <div className="flex flex-col">
                  <div className="flex items-center gap-2">
                    <span className="text-[13px] font-semibold text-white">11:08 PM – 06:45 AM</span>
                    <span className="text-[9px] font-bold px-1.5 py-0.5 rounded border uppercase tracking-wider bg-purple-500/20 text-purple-300 border-purple-400/30">
                      SESSION
                    </span>
                  </div>
                  <span className="text-[11px] text-zinc-400 font-normal">Last Night · Score 84</span>
                </div>
              </div>
              <div className="flex items-baseline gap-1 text-right">
                <span className="text-sm font-bold text-white">7h 37m</span>
              </div>
            </div>

            {/* Stacked Breakdown Cards for the 7h 37m session */}
            {sleepStageLogs.map((item, idx) => (
              <div
                key={idx}
                className="bg-zinc-800/50 hover:bg-zinc-800/80 border border-zinc-800 hover:border-zinc-700/80 rounded-xl p-2.5 px-3 flex items-center justify-between transition-all group"
              >
                <div className="flex items-center gap-3 min-w-0">
                  <div className={`w-8 h-8 rounded-lg ${item.tagBg} flex items-center justify-center ${item.tagColor} flex-shrink-0 group-hover:scale-105 transition-transform`}>
                    <span className="material-symbols-outlined text-[17px]">{item.icon}</span>
                  </div>
                  <div className="flex flex-col min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-[12.5px] font-semibold text-white truncate">{item.stage}</span>
                      <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded border uppercase tracking-wider ${item.tagBg} ${item.tagColor}`}>
                        {item.tag}
                      </span>
                    </div>
                    <span className="text-[11px] text-zinc-400 truncate">{item.timeRange}</span>
                  </div>
                </div>

                <div className="flex flex-col items-end flex-shrink-0 pl-2">
                  <span className="text-xs font-bold text-white group-hover:text-purple-300 transition-colors">
                    {item.duration}
                  </span>
                  <span className="text-[10px] text-zinc-400 font-medium">{item.percentage}</span>
                </div>
              </div>
            ))}

            {/* Footer info note */}
            <div className="flex items-center gap-2 px-3 py-2 rounded-xl bg-zinc-800/40 border border-zinc-800/70 text-xs mt-1">
              <span className="material-symbols-outlined text-purple-400 text-[16px]">verified</span>
              <span className="text-zinc-300">
                Log completed at <strong className="text-white font-medium">06:45 AM</strong> · Efficiency was 91%
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
