import React from 'react';
import Link from 'next/link';

export default function FallDetectionAlerts() {
  const alerts = [
    { id: 1, title: 'Fall detected', time: 'Today, 03 Sep 2026 · 10:42 AM', tag: 'Recent' },
    { id: 2, title: 'Fall detected', time: '28 Aug 2026 · 04:15 PM' },
    { id: 3, title: 'Fall detected', time: '14 Aug 2026 · 08:09 AM' },
    { id: 4, title: 'Fall detected', time: '02 Jul 2026 · 06:33 PM' },
    { id: 5, title: 'Fall detected', time: '19 Jun 2026 · 11:18 AM' },
  ];

  return (
    <div className="min-h-screen bg-[#070708] text-white flex justify-center selection:bg-[#4A1985] selection:text-white pb-28">
      <div className="w-full max-w-md min-h-screen flex flex-col relative px-4 pt-4">
        {/* Top Header */}
        <header className="sticky top-0 z-30 bg-[#070708]/90 backdrop-blur-md pt-10 pb-4 border-b border-white/[0.06] flex items-center gap-3.5">
          <Link
            href="/profile"
            aria-label="Go back"
            className="w-10 h-10 -ml-1 flex items-center justify-center rounded-xl bg-[#161618] border border-white/5 text-neutral-300 hover:text-white active:scale-95 transition-all"
          >
            <span className="material-symbols-outlined text-[22px]">arrow_back</span>
          </Link>
          <div>
            <h1 className="text-[20px] font-bold tracking-tight text-white leading-tight">
              Fall Detection Alerts
            </h1>
            <p className="text-[13px] font-medium text-neutral-400 mt-0.5">
              Your recorded fall alerts
            </p>
          </div>
        </header>

        {/* Main Content Area */}
        <main className="flex-1 pt-5 space-y-3">
          <div className="flex flex-col gap-2.5">
            {alerts.map((alert) => (
              <div
                key={alert.id}
                className="group flex items-center justify-between p-4 rounded-2xl bg-[#161618] border border-white/5 hover:border-white/10 active:scale-[0.99] transition-all cursor-pointer shadow-sm"
              >
                <div className="flex items-center gap-3.5">
                  <div className="w-11 h-11 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center text-neutral-300 group-hover:scale-105 transition-transform">
                    <span className="material-symbols-outlined text-[22px]">personal_injury</span>
                  </div>
                  <div className="space-y-0.5">
                    <div className="flex items-center gap-2">
                      <span className="text-[15px] font-semibold text-white tracking-tight">
                        {alert.title}
                      </span>
                      {alert.tag && (
                        <span className="px-1.5 py-0.5 text-[10px] font-medium tracking-wide uppercase bg-white/5 text-neutral-300 rounded border border-white/10">
                          {alert.tag}
                        </span>
                      )}
                    </div>
                    <p className="text-[12.5px] font-normal text-neutral-400">{alert.time}</p>
                  </div>
                </div>
                <span className="material-symbols-outlined text-neutral-500 group-hover:text-neutral-300 text-[20px] transition-colors">
                  chevron_right
                </span>
              </div>
            ))}
          </div>

          <div className="mt-8 p-4 rounded-2xl bg-zinc-900/40 border border-zinc-800/50 text-center">
            <span className="material-symbols-outlined text-emerald-400 text-3xl mb-1">shield</span>
            <p className="text-xs text-neutral-400 leading-relaxed">
              Automated sensor fall detection is active. In case of an emergency, your primary emergency contact is notified instantly.
            </p>
          </div>
        </main>
      </div>

      {/* Persistent Bottom Navigation */}
      <nav className="fixed bottom-0 left-0 right-0 z-50 bg-[#0a0a0c]/95 backdrop-blur-xl border-t border-white/[0.08] px-6 py-2.5 pb-[max(0.75rem,env(safe-area-inset-bottom))] flex justify-around items-center">
        <Link href="/dashboard" className="text-neutral-400 hover:text-neutral-200 font-medium flex flex-col items-center gap-1 px-3 py-1 transition-colors duration-200">
          <span className="material-symbols-outlined text-[22px] leading-none" style={{ fontVariationSettings: "'FILL' 0" }}>home</span>
          <span className="text-[11px] tracking-normal whitespace-nowrap">Home</span>
        </Link>
        <Link href="/weather" className="text-neutral-400 hover:text-neutral-200 font-medium flex flex-col items-center gap-1 px-3 py-1 transition-colors duration-200">
          <span className="material-symbols-outlined text-[22px] leading-none" style={{ fontVariationSettings: "'FILL' 0" }}>partly_cloudy_day</span>
          <span className="text-[11px] tracking-normal whitespace-nowrap">Weather</span>
        </Link>
        <Link href="/sleep" className="text-neutral-400 hover:text-neutral-200 font-medium flex flex-col items-center gap-1 px-3 py-1 transition-colors duration-200">
          <span className="material-symbols-outlined text-[22px] leading-none" style={{ fontVariationSettings: "'FILL' 0" }}>dark_mode</span>
          <span className="text-[11px] tracking-normal whitespace-nowrap">Sleep</span>
        </Link>
        <Link href="/profile" className="text-neutral-100 font-semibold bg-neutral-800 rounded-xl px-3 py-1 flex flex-col items-center gap-1 transition-all duration-200">
          <span className="material-symbols-outlined text-[22px] leading-none" style={{ fontVariationSettings: "'FILL' 1" }}>person</span>
          <span className="text-[11px] font-semibold tracking-normal whitespace-nowrap">Profile</span>
        </Link>
      </nav>
    </div>
  );
}
