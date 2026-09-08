import db from '@/lib/db';
import { redirect } from 'next/navigation';
import Link from 'next/link';
import HeartRateTrends from './HeartRateTrends';

export default function Dashboard() {
  const users = db.prepare('SELECT * FROM users LIMIT 1').all() as any[];
  if (users.length === 0) {
    redirect('/onboarding/personal-details');
  }
  const user = users[0];

  return (
    <>
      <header className="sticky top-0 z-40 w-full bg-[#0a0a0c]/90 backdrop-blur-md border-b border-white/[0.06] px-5 py-3.5 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full border border-white/10 bg-primary flex items-center justify-center font-bold text-[18px] text-on-primary">
            {user.first_name[0]}{user.last_name ? user.last_name[0] : ''}
          </div>
          <div>
            <p className="text-[12px] font-medium tracking-wider text-neutral-400 uppercase">
              {new Date().toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}
            </p>
            <h1 className="text-[18px] font-semibold text-white tracking-tight leading-snug">Good Morning, {user.first_name}</h1>
          </div>
        </div>
        <Link href="/profile" className="w-10 h-10 rounded-full bg-neutral-900/80 border border-white/10 flex items-center justify-center text-neutral-300 hover:text-white transition-colors duration-200">
          <span className="material-symbols-outlined text-[20px]" style={{fontVariationSettings: "'FILL' 0"}}>person</span>
        </Link>
      </header>
      
      <main className="pt-4 pb-28">
        <section className="px-5 mb-4">
          <div className="bg-[#1C1C1E] metric-group-glow rounded-xl p-6 flex items-center justify-between gap-4 min-h-[190px]">
            <div className="relative w-36 h-36 flex items-center justify-center flex-shrink-0">
              <svg className="absolute inset-0 w-full h-full transform -rotate-90" viewBox="0 0 120 120">
                <circle cx="60" cy="60" fill="none" r="50" stroke="rgba(34, 197, 94, 0.15)" strokeWidth="9"></circle>
                <circle cx="60" cy="60" fill="none" r="50" stroke="#22C55E" strokeDasharray="314.159" strokeDashoffset="94.247" strokeLinecap="round" strokeWidth="9"></circle>
                <circle cx="60" cy="60" fill="none" r="37" stroke="rgba(59, 130, 246, 0.15)" strokeWidth="9"></circle>
                <circle cx="60" cy="60" fill="none" r="37" stroke="#3B82F6" strokeDasharray="232.477" strokeDashoffset="37.196" strokeLinecap="round" strokeWidth="9"></circle>
              </svg>
              <div className="flex flex-col items-center text-center z-10">
                <span className="text-3xl font-bold text-on-surface leading-none">--</span>
                <span className="text-[11px] font-semibold tracking-widest text-on-surface-variant mt-1 uppercase">SCORE</span>
              </div>
            </div>
            <div className="flex flex-col justify-center gap-4 flex-1 pl-4 border-l border-[rgba(255,255,255,0.05)]">
              <div className="flex flex-col">
                <div className="flex items-center gap-1.5 mb-1">
                  <div className="w-2 h-2 rounded-full bg-[#22C55E]"></div>
                  <span className="text-[12px] font-semibold tracking-wider text-on-surface-variant uppercase">CALORIES</span>
                </div>
                <div className="flex items-baseline gap-1.5 whitespace-nowrap">
                  <span className="text-2xl font-bold text-on-surface leading-tight">--</span>
                  <span className="text-[12px] text-on-surface-variant font-medium">/ {user.daily_calories_goal} kcal</span>
                </div>
                <span className="text-[12px] text-[#22C55E] font-medium mt-0.5 whitespace-nowrap">Syncing...</span>
              </div>
              <div className="w-full h-px bg-[rgba(255,255,255,0.05)]"></div>
              <div className="flex flex-col">
                <div className="flex items-center gap-1.5 mb-1">
                  <div className="w-2 h-2 rounded-full bg-[#3B82F6]"></div>
                  <span className="text-[12px] font-semibold tracking-wider text-on-surface-variant uppercase">STEPS</span>
                </div>
                <div className="flex items-baseline gap-1.5 whitespace-nowrap">
                  <span className="text-2xl font-bold text-on-surface leading-tight">--</span>
                  <span className="text-[12px] text-on-surface-variant font-medium">/ {user.daily_steps_goal}</span>
                </div>
                <span className="text-[12px] text-[#3B82F6] font-medium mt-0.5 whitespace-nowrap">Syncing...</span>
              </div>
            </div>
          </div>
        </section>
        
        <section className="px-5 mb-4">
          <Link href="/blood-oxygen" className="block bg-[#1C1C1E] metric-group-glow rounded-xl p-6 relative overflow-hidden flex flex-col justify-between border border-[rgba(255,255,255,0.05)]">
            <div className="flex justify-between items-center mb-1">
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{backgroundColor: 'rgba(245, 158, 11, 0.12)'}}>
                  <span className="material-symbols-outlined text-[20px]" style={{color: '#f59e0b'}}>air</span>
                </div>
                <h2 className="text-base font-semibold text-on-surface tracking-tight">Blood Oxygen</h2>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[12px] font-semibold px-2.5 py-0.5 rounded-full whitespace-nowrap tracking-wide border" style={{backgroundColor: 'rgba(245, 158, 11, 0.15)', color: '#fbbf24', borderColor: 'rgba(245, 158, 11, 0.25)'}}>WAITING</span>
                <span className="material-symbols-outlined text-on-surface-variant opacity-60 text-base">chevron_right</span>
              </div>
            </div>
            <div className="flex items-baseline gap-1.5 mt-3 mb-1">
              <span className="text-4xl font-bold tracking-tight text-on-surface">--</span>
              <span className="text-xl font-semibold" style={{color: '#f59e0b'}}>%</span>
            </div>
            <p className="text-[12px] font-normal text-on-surface-variant leading-relaxed mb-2">Connect your hardware sensor.</p>
            <div className="flex items-center gap-1.5 pt-2 border-t border-[rgba(255,255,255,0.05)]">
              <div className="w-1.5 h-1.5 rounded-full" style={{backgroundColor: '#f59e0b'}}></div>
              <span className="text-[11px] font-normal text-on-surface-variant opacity-60">Updated just now</span>
            </div>
          </Link>
        </section>

        <section className="px-5 mb-4">
          <div className="grid grid-cols-2 gap-3">
            <Link href="/weather" className="bg-[#1C1C1E] metric-group-glow rounded-xl p-4 flex flex-col justify-between border border-[rgba(255,255,255,0.05)]">
              <div className="flex items-center gap-2 mb-2">
                <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{backgroundColor: 'rgba(56, 189, 248, 0.12)'}}>
                  <span className="material-symbols-outlined text-[18px]" style={{color: '#38bdf8'}}>wb_sunny</span>
                </div>
                <h4 className="text-[14px] font-semibold text-on-surface tracking-tight truncate">Weather & Heatwave</h4>
              </div>
              <p className="text-[12px] text-on-surface-variant leading-snug mb-3">38°C · Feels like 42°C · High heat risk in {user.location.split(',')[0]}</p>
              <div className="flex items-center gap-1 text-[12px] font-medium" style={{color: '#38bdf8'}}>
                <span>View alerts</span>
                <span className="material-symbols-outlined text-[14px] leading-none">arrow_forward</span>
              </div>
            </Link>
            <Link href="/sleep" className="bg-[#1C1C1E] metric-group-glow rounded-xl p-4 flex flex-col justify-between border border-[rgba(255,255,255,0.05)]">
              <div className="flex items-center gap-2 mb-2">
                <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{backgroundColor: 'rgba(168, 85, 247, 0.12)'}}>
                  <span className="material-symbols-outlined text-[18px]" style={{color: '#a855f7'}}>bedtime</span>
                </div>
                <h4 className="text-[14px] font-semibold text-on-surface tracking-tight truncate">Sleep Insights</h4>
              </div>
              <p className="text-[12px] text-on-surface-variant leading-snug mb-3">Sleep score 84 · 7h 42m</p>
              <div className="flex items-center gap-1 text-[12px] font-medium" style={{color: '#a855f7'}}>
                <span>View insights</span>
                <span className="material-symbols-outlined text-[14px] leading-none">arrow_forward</span>
              </div>
            </Link>
          </div>
        </section>

        <section className="px-5 mb-4">
          <Link href="/heart-rate" className="block bg-[#1C1C1E] metric-group-glow rounded-xl p-6 relative overflow-hidden min-h-[190px] flex flex-col justify-between">
            <div className="absolute bottom-0 left-0 right-0 h-20 pointer-events-none overflow-hidden z-0">
              <svg className="w-full h-full" preserveAspectRatio="none" viewBox="0 0 500 80">
                <defs>
                  <linearGradient id="ecgLineGrad" x1="0%" y1="0%" x2="100%" y2="0%">
                    <stop offset="0%" stopColor="#EF4444" stopOpacity="0.1"></stop>
                    <stop offset="25%" stopColor="#EF4444" stopOpacity="0.4"></stop>
                    <stop offset="50%" stopColor="#EF4444" stopOpacity="0.9"></stop>
                    <stop offset="75%" stopColor="#EF4444" stopOpacity="0.5"></stop>
                    <stop offset="100%" stopColor="#EF4444" stopOpacity="0.15"></stop>
                  </linearGradient>
                  <linearGradient id="ecgAreaGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#EF4444" stopOpacity="0.12"></stop>
                    <stop offset="100%" stopColor="#EF4444" stopOpacity="0"></stop>
                  </linearGradient>
                  <filter id="ecgGlow" x="-20%" y="-20%" width="140%" height="140%">
                    <feGaussianBlur stdDeviation="2" result="blur"></feGaussianBlur>
                    <feMerge>
                      <feMergeNode in="blur"></feMergeNode>
                      <feMergeNode in="SourceGraphic"></feMergeNode>
                    </feMerge>
                  </filter>
                </defs>
                <path d="M0 45 L50 45 L65 45 L72 38 L80 52 L88 45 L130 45 L142 42 L148 48 L154 45 L175 45 L182 43 L190 45 L200 45 L208 50 L216 12 L225 72 L234 35 L242 50 L250 45 L290 45 L300 40 L310 49 L320 45 L350 45 L358 50 L366 16 L375 70 L384 38 L392 48 L400 45 L430 45 L445 40 L455 48 L465 45 L500 45 L500 80 L0 80 Z" fill="url(#ecgAreaGrad)"></path>
                <path d="M0 45 L50 45 L65 45 L72 38 L80 52 L88 45 L130 45 L142 42 L148 48 L154 45 L175 45 L182 43 L190 45 L200 45 L208 50 L216 12 L225 72 L234 35 L242 50 L250 45 L290 45 L300 40 L310 49 L320 45 L350 45 L358 50 L366 16 L375 70 L384 38 L392 48 L400 45 L430 45 L445 40 L455 48 L465 45 L500 45" fill="none" stroke="url(#ecgLineGrad)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" filter="url(#ecgGlow)"></path>
              </svg>
            </div>
            <div className="relative z-10">
              <div className="flex justify-between items-center mb-3">
                <div className="flex items-center gap-2">
                  <span className="material-symbols-outlined text-[#EF4444]" style={{fontVariationSettings: "'FILL' 1"}}>favorite</span>
                  <h2 className="text-xl font-semibold text-on-surface tracking-tight">Heart Rate</h2>
                </div>
                <span className="bg-neutral-800 text-neutral-400 text-[12px] font-bold px-3 py-1 rounded-full whitespace-nowrap tracking-wider uppercase">WAITING</span>
              </div>
              <div className="flex items-baseline gap-2 mb-2">
                <span className="text-[48px] font-bold text-neutral-400 leading-none tracking-tight">--</span>
                <span className="text-[16px] font-semibold text-on-surface-variant tracking-wide">BPM</span>
              </div>
              <p className="text-[14px] font-normal text-on-surface-variant max-w-[80%] leading-relaxed">Waiting for ML model analysis.</p>
            </div>
          </Link>
        </section>

        {/* Trends Section */}
        <section className="px-5 mb-8">
          <h3 className="text-xl font-bold text-on-surface mb-4 tracking-tight">Trends</h3>
          <HeartRateTrends />
        </section>
      </main>

      <nav className="fixed bottom-0 left-0 right-0 z-50 bg-[#0a0a0c]/95 backdrop-blur-xl border-t border-white/[0.08] px-6 py-2.5 pb-[max(0.75rem,env(safe-area-inset-bottom))] flex justify-around items-center">
        <Link href="/dashboard" className="text-red-500 font-semibold bg-red-500/10 border border-red-500/20 rounded-xl px-3 py-1 flex flex-col items-center gap-1 transition-all duration-200">
          <span className="material-symbols-outlined text-[22px] leading-none text-red-500" style={{fontVariationSettings: "'FILL' 1"}}>home</span>
          <span className="text-[11px] font-semibold tracking-normal whitespace-nowrap text-red-500">Home</span>
        </Link>
        <Link href="/weather" className="text-neutral-400 hover:text-neutral-200 font-medium flex flex-col items-center gap-1 px-3 py-1 transition-colors duration-200">
          <span className="material-symbols-outlined text-[22px] leading-none" style={{fontVariationSettings: "'FILL' 0"}}>partly_cloudy_day</span>
          <span className="text-[11px] tracking-normal whitespace-nowrap">Weather</span>
        </Link>
        <Link href="/sleep" className="text-neutral-400 hover:text-neutral-200 font-medium flex flex-col items-center gap-1 px-3 py-1 transition-colors duration-200">
          <span className="material-symbols-outlined text-[22px] leading-none" style={{fontVariationSettings: "'FILL' 0"}}>dark_mode</span>
          <span className="text-[11px] tracking-normal whitespace-nowrap">Sleep</span>
        </Link>
        <Link href="/profile" className="text-neutral-400 hover:text-neutral-200 font-medium flex flex-col items-center gap-1 px-3 py-1 transition-colors duration-200">
          <span className="material-symbols-outlined text-[22px] leading-none" style={{fontVariationSettings: "'FILL' 0"}}>person</span>
          <span className="text-[11px] tracking-normal whitespace-nowrap">Profile</span>
        </Link>
      </nav>
    </>
  );
}
