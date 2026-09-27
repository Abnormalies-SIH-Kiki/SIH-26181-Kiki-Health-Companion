'use client';

import React, { useState, useEffect } from 'react';

export default function SleepNotificationCard() {
  const [isVisible, setIsVisible] = useState(false);
  const [isDismissed, setIsDismissed] = useState(false);

  useEffect(() => {
    // Slight delay so the pop-out animation visibly triggers upon page transition/mount
    const timer = setTimeout(() => {
      setIsVisible(true);
    }, 120);
    return () => clearTimeout(timer);
  }, []);

  if (isDismissed) return null;

  return (
    <div
      className={`transition-all duration-500 cubic-bezier(0.34, 1.56, 0.64, 1) transform mb-5 ${
        isVisible
          ? 'opacity-100 scale-100 translate-y-0'
          : 'opacity-0 scale-90 -translate-y-3 pointer-events-none'
      }`}
    >
      <div className="relative overflow-hidden flex items-center gap-3 bg-gradient-to-r from-indigo-950/60 via-slate-900/80 to-indigo-950/50 border border-indigo-500/30 rounded-xl p-3.5 shadow-lg shadow-indigo-950/30 backdrop-blur-md group hover:border-indigo-500/50 transition-all">
        {/* Subtle ambient glow flare */}
        <div className="absolute -left-6 -top-6 w-20 h-20 bg-indigo-500/15 rounded-full blur-xl pointer-events-none"></div>

        {/* Icon with pulsing radar wave */}
        <div className="relative w-9 h-9 rounded-full bg-indigo-500/20 border border-indigo-400/30 flex items-center justify-center text-indigo-300 flex-shrink-0 shadow-inner">
          <span className="material-symbols-outlined text-[19px] animate-pulse">notifications_active</span>
          <span className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full bg-indigo-400 ring-2 ring-zinc-950 animate-ping"></span>
          <span className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full bg-indigo-400 ring-2 ring-zinc-950"></span>
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-[10px] font-bold uppercase tracking-wider text-indigo-400 bg-indigo-500/15 px-1.5 py-0.5 rounded border border-indigo-400/20">
              New Session Logged
            </span>
            <span className="text-[10px] text-zinc-500 font-medium">Just now</span>
          </div>
          <p className="text-[12.5px] text-zinc-200 font-medium leading-snug">
            Recent sleep logged: <strong className="text-indigo-300 font-semibold">7h 37m</strong> from 11:08 PM.
          </p>
        </div>

        {/* Dismiss Button */}
        <button
          type="button"
          onClick={() => setIsDismissed(true)}
          className="text-zinc-500 hover:text-zinc-300 p-1 rounded-lg hover:bg-white/5 transition flex-shrink-0"
          aria-label="Dismiss notification"
        >
          <span className="material-symbols-outlined text-[16px]">close</span>
        </button>
      </div>
    </div>
  );
}
