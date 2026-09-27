import db from '@/lib/db';
import { redirect } from 'next/navigation';
import Link from 'next/link';
import DeleteAccountButton from './DeleteAccountButton';

export default function Profile() {
  const users = db.prepare('SELECT * FROM users LIMIT 1').all() as any[];
  if (users.length === 0) {
    redirect('/onboarding/personal-details');
  }
  const user = users[0];

  return (
    <>
      <div className="flex flex-col relative w-full pt-4 pb-28 bg-primary-container min-h-screen px-4 transition-colors">
        
        <header className="flex items-center justify-between py-3 mb-2 pt-10">
          <h1 className="text-xl font-bold tracking-tight text-on-surface">Profile</h1>
        </header>

        <div className="bg-surface-container border border-cyan-500/20 rounded-2xl p-4 mb-5 shadow-lg shadow-black/10 dark:shadow-black/40 relative overflow-hidden transition-colors">
          <div className="absolute -top-10 -right-10 w-32 h-32 bg-cyan-500/15 rounded-full blur-3xl pointer-events-none"></div>

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3.5">
              <div className="relative">
                <div className="w-14 h-14 rounded-full bg-gradient-to-tr from-cyan-400 via-blue-500 to-indigo-600 p-[2px] shadow-[0_0_15px_rgba(6,182,212,0.35)]">
                  <div className="w-full h-full rounded-full bg-surface flex items-center justify-center overflow-hidden border border-border-subtle">
                    <span className="text-lg font-bold text-on-surface tracking-wide">{user.first_name[0]}{user.last_name ? user.last_name[0] : ''}</span>
                  </div>
                </div>
                <span className="absolute bottom-0 right-0 w-3.5 h-3.5 bg-cyan-400 border-2 border-surface-container rounded-full shadow-[0_0_8px_rgba(6,182,212,0.8)]"></span>
              </div>

              <div>
                <div className="flex items-center gap-2">
                  <h2 className="text-lg font-bold text-on-surface tracking-tight leading-tight">{user.first_name} {user.last_name}</h2>
                  <span className="bg-cyan-950/80 border border-cyan-500/30 text-cyan-300 text-[11px] font-semibold px-2.5 py-0.5 rounded-full inline-flex items-center gap-1.5 shadow-[0_0_10px_rgba(6,182,212,0.2)]">
                    <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse"></span>
                    Monitoring
                  </span>
                </div>
                <p className="text-xs text-zinc-400 mt-1 font-normal">{user.age} yrs • {user.gender}</p>
                <div className="flex flex-wrap items-center gap-2 mt-2">
                  <span className="bg-zinc-800/80 border border-zinc-700/50 text-zinc-300 text-[10px] font-medium px-2 py-0.5 rounded-md flex items-center gap-1 shadow-sm">
                    <span className="material-symbols-outlined text-[12px] text-zinc-400">monitor_weight</span>
                    {user.weight ? `${user.weight} kg` : 'N/A'}
                  </span>
                  <span className="bg-zinc-800/80 border border-zinc-700/50 text-zinc-300 text-[10px] font-medium px-2 py-0.5 rounded-md flex items-center gap-1 shadow-sm">
                    <span className="material-symbols-outlined text-[12px] text-zinc-400">height</span>
                    {user.height ? `${user.height} cm` : 'N/A'}
                  </span>
                  <span className="bg-zinc-800/80 border border-zinc-700/50 text-zinc-300 text-[10px] font-medium px-2 py-0.5 rounded-md flex items-center gap-1 shadow-sm">
                    <span className="material-symbols-outlined text-[12px] text-red-400/80">bloodtype</span>
                    {user.blood_group || 'N/A'}
                  </span>
                </div>
              </div>
            </div>
          </div>

          <div className="mt-4 pt-3 border-t border-zinc-800/80 grid grid-cols-3 divide-x divide-zinc-800/80 text-center">
            <div className="px-2">
              <span className="block text-[11px] font-medium text-zinc-400">Heart Rate</span>
              <span className="text-sm font-semibold text-zinc-100 flex items-center justify-center gap-1 mt-0.5">
                <span className="text-red-400 text-xs">♥</span> 100 <span className="text-[10px] text-zinc-500 font-normal">bpm</span>
              </span>
            </div>
            <div className="px-2">
              <span className="block text-[11px] font-medium text-zinc-400">Sleep Score</span>
              <span className="text-sm font-semibold text-cyan-400 flex items-center justify-center gap-1 mt-0.5">
                <span className="w-1.5 h-1.5 rounded-full bg-cyan-400"></span> 84<span className="text-[10px] text-zinc-500 font-normal">/100</span>
              </span>
            </div>
            <div className="px-2">
              <span className="block text-[11px] font-medium text-zinc-400">Emergency</span>
              <span className="text-sm font-semibold text-emerald-400 flex items-center justify-center gap-1 mt-0.5">
                <span className="material-symbols-outlined text-[14px]">shield</span> Ready
              </span>
            </div>
          </div>
        </div>

        <div className="mb-5">
          <div className="flex items-center justify-between px-1 mb-2.5">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-cyan-400/90 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-cyan-400"></span>
              Health & Safety Tools
            </h3>
            <span className="text-[11px] text-zinc-500 font-medium">4 Services Active</span>
          </div>

          <div className="bg-surface-container border border-border-subtle hover:border-cyan-500/20 rounded-2xl overflow-hidden divide-y divide-border-subtle shadow-lg shadow-black/10 dark:shadow-black/30 transition-colors">
            
            <Link href="/heart-rate-alerts" className="flex items-center justify-between p-3.5 hover:bg-zinc-800/40 active:bg-zinc-800/70 transition-colors group">
              <div className="flex items-center gap-3.5">
                <div className="w-10 h-10 rounded-xl bg-cyan-950/40 border border-cyan-500/20 flex items-center justify-center text-cyan-400 group-hover:scale-105 group-hover:border-cyan-400/40 group-hover:shadow-[0_0_10px_rgba(6,182,212,0.25)] transition-all">
                  <span className="material-symbols-outlined text-[22px]">ecg_heart</span>
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-zinc-100 group-hover:text-cyan-300 transition-colors">Heart Rate Alerts</span>
                    <span className="bg-cyan-950/70 text-cyan-300 text-[10px] font-medium px-2 py-0.5 rounded border border-cyan-500/30">High & Low</span>
                  </div>
                  <p className="text-xs text-zinc-400 mt-0.5">Review high and low heart rate events</p>
                </div>
              </div>
              <span className="material-symbols-outlined text-zinc-500 group-hover:text-cyan-300 text-[18px] transition-transform group-hover:translate-x-0.5">chevron_right</span>
            </Link>

            <Link href="/fall-detection" className="flex items-center justify-between p-3.5 hover:bg-zinc-800/40 active:bg-zinc-800/70 transition-colors group">
              <div className="flex items-center gap-3.5">
                <div className="w-10 h-10 rounded-xl bg-cyan-950/40 border border-cyan-500/20 flex items-center justify-center text-cyan-400 group-hover:scale-105 group-hover:border-cyan-400/40 group-hover:shadow-[0_0_10px_rgba(6,182,212,0.25)] transition-all">
                  <span className="material-symbols-outlined text-[22px]">personal_injury</span>
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-zinc-100 group-hover:text-cyan-300 transition-colors">Fall Detection</span>
                    <span className="bg-emerald-950/60 border border-emerald-800/40 text-emerald-400 text-[10px] font-medium px-1.5 py-0.2 rounded">Enabled</span>
                  </div>
                  <p className="text-xs text-zinc-400 mt-0.5">View alerts and emergency contacts</p>
                </div>
              </div>
              <span className="material-symbols-outlined text-zinc-500 group-hover:text-cyan-300 text-[18px] transition-transform group-hover:translate-x-0.5">chevron_right</span>
            </Link>

            <Link href="/health-report" className="flex items-center justify-between p-3.5 hover:bg-zinc-800/40 active:bg-zinc-800/70 transition-colors group cursor-pointer block">
              <div className="flex items-center gap-3.5">
                <div className="w-10 h-10 rounded-xl bg-rose-950/40 border border-rose-500/20 flex items-center justify-center text-rose-400 group-hover:scale-105 group-hover:border-rose-400/40 group-hover:shadow-[0_0_10px_rgba(244,63,94,0.25)] transition-all">
                  <span className="material-symbols-outlined text-[22px]">description</span>
                </div>
                <div>
                  <span className="text-sm font-semibold text-zinc-100 group-hover:text-rose-300 transition-colors">Generate Health Report</span>
                  <p className="text-xs text-zinc-400 mt-0.5">Create and download your health PDF</p>
                </div>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="text-[11px] font-medium text-rose-300 bg-rose-950/80 border border-rose-500/40 px-2 py-0.5 rounded shadow-[0_0_6px_rgba(244,63,94,0.2)]">PDF</span>
                <span className="material-symbols-outlined text-zinc-500 group-hover:text-rose-300 text-[18px] transition-transform group-hover:translate-x-0.5">chevron_right</span>
              </div>
            </Link>

            <div className="flex items-center justify-between p-3.5 hover:bg-zinc-800/40 active:bg-zinc-800/70 transition-colors group cursor-pointer">
              <div className="flex items-center gap-3.5">
                <div className="w-10 h-10 rounded-xl bg-cyan-950/40 border border-cyan-500/20 flex items-center justify-center text-cyan-400 group-hover:scale-105 group-hover:border-cyan-400/40 group-hover:shadow-[0_0_10px_rgba(6,182,212,0.25)] transition-all">
                  <span className="material-symbols-outlined text-[22px]">calendar_month</span>
                </div>
                <div>
                  <span className="text-sm font-semibold text-zinc-100 group-hover:text-cyan-300 transition-colors">Care Plan & Schedule</span>
                  <p className="text-xs text-zinc-400 mt-0.5">Manage care routines, reminders, and appointments</p>
                </div>
              </div>
              <span className="material-symbols-outlined text-zinc-500 group-hover:text-cyan-300 text-[18px] transition-transform group-hover:translate-x-0.5">chevron_right</span>
            </div>

          </div>
        </div>

        <div className="mb-4">
          <div className="px-1 mb-2.5">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-cyan-400/90 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-cyan-400"></span>
              Account & Preferences
            </h3>
          </div>

          <div className="bg-surface-container border border-border-subtle hover:border-cyan-500/20 rounded-2xl overflow-hidden divide-y divide-border-subtle shadow-lg shadow-black/10 dark:shadow-black/30 transition-colors">
            <Link href="/profile/edit" className="flex items-center justify-between p-3.5 hover:bg-zinc-800/40 active:bg-zinc-800/70 transition-colors group">
              <div className="flex items-center gap-3.5">
                <div className="w-8 h-8 rounded-lg bg-cyan-950/50 border border-cyan-500/25 flex items-center justify-center text-cyan-400 group-hover:border-cyan-400/40 transition-colors">
                  <span className="material-symbols-outlined text-[19px]">person</span>
                </div>
                <div>
                  <span className="text-sm font-medium text-zinc-200 group-hover:text-cyan-200 transition-colors">Edit Profile</span>
                  <p className="text-[11px] text-zinc-400">Update your personal details</p>
                </div>
              </div>
              <span className="material-symbols-outlined text-zinc-500 group-hover:text-cyan-300 text-[18px]">chevron_right</span>
            </Link>

            <Link href="/profile/edit-goals" className="flex items-center justify-between p-3.5 hover:bg-zinc-800/40 active:bg-zinc-800/70 transition-colors group">
              <div className="flex items-center gap-3.5">
                <div className="w-8 h-8 rounded-lg bg-cyan-950/50 border border-cyan-500/25 flex items-center justify-center text-cyan-400 group-hover:border-cyan-400/40 transition-colors">
                  <span className="material-symbols-outlined text-[19px]">track_changes</span>
                </div>
                <div>
                  <span className="text-sm font-medium text-zinc-200 group-hover:text-cyan-200 transition-colors">Edit Goal Details</span>
                  <p className="text-[11px] text-zinc-400">Update your health and wellness targets</p>
                </div>
              </div>
              <span className="material-symbols-outlined text-zinc-500 group-hover:text-cyan-300 text-[18px]">chevron_right</span>
            </Link>

            <div className="flex items-center justify-between p-3.5 hover:bg-zinc-800/40 active:bg-zinc-800/70 transition-colors group cursor-pointer">
              <div className="flex items-center gap-3.5">
                <div className="w-8 h-8 rounded-lg bg-cyan-950/50 border border-cyan-500/25 flex items-center justify-center text-cyan-400 group-hover:border-cyan-400/40 transition-colors">
                  <span className="material-symbols-outlined text-[19px]">lock</span>
                </div>
                <div>
                  <span className="text-sm font-medium text-zinc-200 group-hover:text-cyan-200 transition-colors">Privacy & Data</span>
                  <p className="text-[11px] text-zinc-400">Control data permissions and sharing</p>
                </div>
              </div>
              <span className="material-symbols-outlined text-zinc-500 group-hover:text-cyan-300 text-[18px]">chevron_right</span>
            </div>

            <DeleteAccountButton />
          </div>
        </div>
      </div>

      <nav className="fixed bottom-0 left-0 right-0 z-50 bg-primary-container/95 backdrop-blur-xl border-t border-border-subtle px-6 py-2.5 pb-[max(0.75rem,env(safe-area-inset-bottom))] flex justify-around items-center">
        <Link href="/dashboard" className="text-neutral-400 hover:text-neutral-200 font-medium flex flex-col items-center gap-1 px-3 py-1 transition-colors duration-200">
          <span className="material-symbols-outlined text-[22px] leading-none" style={{fontVariationSettings: "'FILL' 0"}}>home</span>
          <span className="text-[11px] tracking-normal whitespace-nowrap">Home</span>
        </Link>
        <Link href="/weather" className="text-neutral-400 hover:text-neutral-200 font-medium flex flex-col items-center gap-1 px-3 py-1 transition-colors duration-200">
          <span className="material-symbols-outlined text-[22px] leading-none" style={{fontVariationSettings: "'FILL' 0"}}>partly_cloudy_day</span>
          <span className="text-[11px] tracking-normal whitespace-nowrap">Weather</span>
        </Link>
        <Link href="/sleep" className="text-neutral-400 hover:text-neutral-200 font-medium flex flex-col items-center gap-1 px-3 py-1 transition-colors duration-200">
          <span className="material-symbols-outlined text-[22px] leading-none" style={{fontVariationSettings: "'FILL' 0"}}>dark_mode</span>
          <span className="text-[11px] tracking-normal whitespace-nowrap">Sleep</span>
        </Link>
        <Link href="/profile" className="text-cyan-400 font-semibold bg-cyan-500/10 border border-cyan-500/20 rounded-xl px-3 py-1 flex flex-col items-center gap-1 transition-all duration-200">
          <span className="material-symbols-outlined text-[22px] leading-none text-cyan-400" style={{fontVariationSettings: "'FILL' 1"}}>person</span>
          <span className="text-[11px] font-semibold tracking-normal whitespace-nowrap text-cyan-400">Profile</span>
        </Link>
      </nav>
    </>
  );
}
