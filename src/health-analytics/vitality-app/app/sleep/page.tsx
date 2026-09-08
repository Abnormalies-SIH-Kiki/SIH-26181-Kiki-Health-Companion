import db from '@/lib/db';
import { redirect } from 'next/navigation';
import Link from 'next/link';
import SleepTrendsCard from '@/components/SleepTrendsCard';

export default function SleepInsights() {
  const users = db.prepare('SELECT * FROM users LIMIT 1').all() as any[];
  if (users.length === 0) {
    redirect('/onboarding/personal-details');
  }
  const user = users[0];

  return (
    <>
      <header className="sticky top-0 z-40 w-full bg-[#0a0a0c]/90 backdrop-blur-md border-b border-white/[0.06] px-5 py-3.5 flex items-center justify-between">
        <div className="flex items-center gap-3.5">
          <div className="w-10 h-10 rounded-full border border-white/10 bg-primary flex items-center justify-center font-bold text-[18px] text-on-primary">
            {user.first_name[0]}{user.last_name ? user.last_name[0] : ''}
          </div>
          <div className="flex flex-col">
            <span className="text-[11px] font-medium uppercase tracking-wider text-neutral-400 leading-tight">
              {new Date().toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}
            </span>
            <h1 className="text-lg font-semibold text-white leading-tight mt-0.5">Good Morning, {user.first_name}</h1>
          </div>
        </div>
        <Link href="/profile" className="w-10 h-10 rounded-full bg-neutral-900/80 border border-white/10 flex items-center justify-center text-neutral-300 hover:text-white transition-colors duration-200">
          <span className="material-symbols-outlined text-[20px]" style={{fontVariationSettings: "'FILL' 0"}}>person</span>
        </Link>
      </header>

      <main className="flex-1 w-full max-w-md mx-auto px-4 pt-4 pb-28">
        <div className="flex flex-col mb-4">
          <h2 className="text-2xl font-bold text-white tracking-tight leading-tight">Sleep Insights</h2>
          <p className="text-xs text-zinc-400 font-medium mt-0.5">Waiting for sensor data...</p>
        </div>

        <section className="bg-zinc-900/80 border border-zinc-800/80 rounded-2xl p-5 mb-5 shadow-lg relative overflow-hidden backdrop-blur-sm">
          <div className="absolute -top-14 -right-14 w-44 h-44 rounded-full bg-purple-600/10 blur-3xl pointer-events-none"></div>
          
          <div className="flex items-center gap-5">
            <div className="relative w-24 h-24 flex-shrink-0 flex items-center justify-center">
              <svg className="w-24 h-24 -rotate-90 transform" viewBox="0 0 96 96">
                <defs>
                  <linearGradient id="purpleRing" x1="0%" x2="100%" y1="0%" y2="100%">
                    <stop offset="0%" stopColor="#7C3AED"></stop>
                    <stop offset="100%" stopColor="#9333EA"></stop>
                  </linearGradient>
                </defs>
                <circle cx="48" cy="48" fill="none" r="38" stroke="#27272A" strokeWidth="7"></circle>
                <circle cx="48" cy="48" fill="none" r="38" stroke="url(#purpleRing)" strokeDasharray="238.76" strokeDashoffset="238.76" strokeLinecap="round" strokeWidth="7" className="transition-all duration-1000"></circle>
              </svg>
              <div className="absolute inset-0 flex flex-col items-center justify-center">
                <span className="text-2xl font-bold text-white tracking-tight leading-none">--</span>
                <span className="text-[10px] font-medium text-zinc-400 mt-0.5">/ 100</span>
              </div>
            </div>

            <div className="flex flex-col justify-center flex-1 min-w-0 space-y-2">
              <div className="flex items-center gap-2">
                <div className="w-6 h-6 rounded-md bg-purple-950/70 border border-purple-800/50 flex items-center justify-center text-purple-300 flex-shrink-0">
                  <span className="material-symbols-outlined text-[15px]">dark_mode</span>
                </div>
                <div className="flex items-baseline gap-1.5 flex-wrap">
                  <span className="text-sm font-semibold text-white">--h --m</span>
                  <span className="text-[11px] text-zinc-400">Goal: 8h</span>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-6 h-6 rounded-md bg-purple-950/70 border border-purple-800/50 flex items-center justify-center text-purple-300 flex-shrink-0">
                  <span className="material-symbols-outlined text-[15px]">schedule</span>
                </div>
                <span className="text-xs text-zinc-300 font-medium">--:-- PM – --:-- AM</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-6 h-6 rounded-md bg-purple-950/70 border border-purple-800/50 flex items-center justify-center text-purple-300 flex-shrink-0">
                  <span className="material-symbols-outlined text-[15px]">vital_signs</span>
                </div>
                <div className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-purple-900/30 border border-purple-700/40 text-purple-300 text-[11px] font-medium">
                  <span className="w-1.5 h-1.5 rounded-full bg-purple-400 animate-pulse"></span>
                  <span className="">Syncing Data</span>
                </div>
              </div>
            </div>
          </div>

          <div className="mt-4 pt-3.5 border-t border-zinc-800/90">
            <p className="text-xs sm:text-sm text-zinc-300 leading-relaxed font-normal">
              Connect your smartwatch or hardware tracker to receive personalized sleep insights and analysis.
            </p>
          </div>
        </section>

        <section className="mb-5">
          <SleepTrendsCard />
        </section>

        <section className="mb-5">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-bold text-white tracking-tight">Improve Your Sleep</h2>
            <span className="bg-purple-900/40 text-purple-300 border border-purple-700/40 px-2.5 py-0.5 rounded-full text-xs font-medium">
              Personalised for you
            </span>
          </div>
          
          <div className="space-y-3">
            {/* Protocol Card 1 */}
            <div className="bg-zinc-900/80 border border-zinc-800/80 rounded-2xl p-4 flex flex-col gap-2.5 transition active:scale-[0.99]">
              <div className="flex items-start gap-3">
                <div className="w-10 h-10 rounded-full bg-purple-950/70 border border-purple-800/50 flex items-center justify-center text-purple-300 flex-shrink-0 mt-0.5">
                  <span className="material-symbols-outlined text-[20px]">alarm</span>
                </div>
                <div className="flex-1 min-w-0">
                  <h3 className="text-base font-semibold text-white leading-snug">Maintain Consistent Schedule</h3>
                  <p className="text-xs text-zinc-400 leading-relaxed mt-1">
                    Your bedtime varied by 48 mins this weekend. Sleeping in a 20-min window anchors your circadian rhythm.
                  </p>
                </div>
              </div>
              <button className="self-end px-4 py-2 rounded-xl bg-purple-700 hover:bg-purple-600 active:bg-purple-800 text-white text-xs font-semibold shadow-sm transition" type="button">
                Set Bedtime Reminder
              </button>
            </div>

            {/* Protocol Card 2 */}
            <div className="bg-zinc-900/80 border border-zinc-800/80 rounded-2xl p-4 flex flex-col gap-2.5 transition active:scale-[0.99]">
              <div className="flex items-start gap-3">
                <div className="w-10 h-10 rounded-full bg-purple-950/70 border border-purple-800/50 flex items-center justify-center text-purple-300 flex-shrink-0 mt-0.5">
                  <span className="material-symbols-outlined text-[20px]">thermostat</span>
                </div>
                <div className="flex-1 min-w-0">
                  <h3 className="text-base font-semibold text-white leading-snug">Cool Your Sleep Environment</h3>
                  <p className="text-xs text-zinc-400 leading-relaxed mt-1">
                    Ideal room temperature is 18°C–20°C to initiate natural body cooling for sustained REM cycles.
                  </p>
                </div>
              </div>
              <button className="self-end px-4 py-2 rounded-xl bg-zinc-800 hover:bg-zinc-700 active:bg-zinc-850 text-zinc-200 border border-zinc-700 text-xs font-semibold shadow-sm transition" type="button">
                Learn More
              </button>
            </div>

            {/* Protocol Card 3 */}
            <div className="bg-zinc-900/80 border border-zinc-800/80 rounded-2xl p-4 flex flex-col gap-2.5 transition active:scale-[0.99]">
              <div className="flex items-start gap-3">
                <div className="w-10 h-10 rounded-full bg-purple-950/70 border border-purple-800/50 flex items-center justify-center text-purple-300 flex-shrink-0 mt-0.5">
                  <span className="material-symbols-outlined text-[20px]">screen_lock_portrait</span>
                </div>
                <div className="flex-1 min-w-0">
                  <h3 className="text-base font-semibold text-white leading-snug">Evening Wind-Down & Screen Break</h3>
                  <p className="text-xs text-zinc-400 leading-relaxed mt-1">
                    Blue light suppresses melatonin release by up to 90 mins. Try activating Night Shift by 9:30 PM.
                  </p>
                </div>
              </div>
              <button className="self-end px-4 py-2 rounded-xl bg-purple-700 hover:bg-purple-600 active:bg-purple-800 text-white text-xs font-semibold shadow-sm transition" type="button">
                Try Tonight
              </button>
            </div>

            {/* Protocol Card 4 */}
            <div className="bg-zinc-900/80 border border-zinc-800/80 rounded-2xl p-4 flex flex-col gap-1 transition active:scale-[0.99]">
              <div className="flex items-start gap-3">
                <div className="w-10 h-10 rounded-full bg-purple-950/70 border border-purple-800/50 flex items-center justify-center text-purple-300 flex-shrink-0 mt-0.5">
                  <span className="material-symbols-outlined text-[20px]">wb_sunny</span>
                </div>
                <div className="flex-1 min-w-0">
                  <h3 className="text-base font-semibold text-white leading-snug">Morning Sunlight Exposure</h3>
                  <p className="text-xs text-zinc-400 leading-relaxed mt-1">
                    15 minutes of outdoor daylight before 9:00 AM primes your internal clock for faster sleep onset.
                  </p>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Encouraging Footer Card */}
        <div className="bg-zinc-900/50 border border-zinc-800/60 rounded-xl p-3.5 text-center text-xs text-zinc-400 leading-relaxed mb-6">
          Small, consistent changes can improve your sleep quality and daytime energy over time.
        </div>

      </main>

      <nav className="fixed bottom-0 left-0 right-0 z-50 bg-[#0a0a0c]/95 backdrop-blur-xl border-t border-white/[0.08] px-6 py-2.5 pb-[max(0.75rem,env(safe-area-inset-bottom))] flex justify-around items-center">
        <Link href="/dashboard" className="text-neutral-400 hover:text-neutral-200 font-medium flex flex-col items-center gap-1 px-3 py-1 transition-colors duration-200">
          <span className="material-symbols-outlined text-[22px] leading-none" style={{fontVariationSettings: "'FILL' 0"}}>home</span>
          <span className="text-[11px] tracking-normal whitespace-nowrap">Home</span>
        </Link>
        <Link href="/weather" className="text-neutral-400 hover:text-neutral-200 font-medium flex flex-col items-center gap-1 px-3 py-1 transition-colors duration-200">
          <span className="material-symbols-outlined text-[22px] leading-none" style={{fontVariationSettings: "'FILL' 0"}}>partly_cloudy_day</span>
          <span className="text-[11px] tracking-normal whitespace-nowrap">Weather</span>
        </Link>
        <Link href="/sleep" className="text-purple-400 font-semibold bg-purple-500/10 border border-purple-500/20 rounded-xl px-3 py-1 flex flex-col items-center gap-1 transition-all duration-200">
          <span className="material-symbols-outlined text-[22px] leading-none text-purple-400" style={{fontVariationSettings: "'FILL' 1"}}>dark_mode</span>
          <span className="text-[11px] font-semibold tracking-normal whitespace-nowrap text-purple-400">Sleep</span>
        </Link>
        <Link href="/profile" className="text-neutral-400 hover:text-neutral-200 font-medium flex flex-col items-center gap-1 px-3 py-1 transition-colors duration-200">
          <span className="material-symbols-outlined text-[22px] leading-none" style={{fontVariationSettings: "'FILL' 0"}}>person</span>
          <span className="text-[11px] tracking-normal whitespace-nowrap">Profile</span>
        </Link>
      </nav>
    </>
  );
}
