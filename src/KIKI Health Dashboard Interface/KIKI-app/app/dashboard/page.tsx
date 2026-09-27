import db from '@/lib/db';
import { redirect } from 'next/navigation';
import Link from 'next/link';
import HeartRateTrends from './HeartRateTrends';
import { ThemeToggle } from '@/components/ThemeToggle';
import BodyTemperatureCard from '@/components/BodyTemperatureCard';

function calculateWellnessScore(
  currentSteps: number,
  targetSteps: number,
  currentCalories: number,
  targetCalories: number,
  sleepScore: number,
  restingHR: number,
  spO2: number
) {
  // Activity (30%)
  const stepsCompletion = Math.min(Math.max(currentSteps / targetSteps, 0), 1) * 15;
  const caloriesCompletion = Math.min(Math.max(currentCalories / targetCalories, 0), 1) * 15;
  const activityScore = stepsCompletion + caloriesCompletion;

  // Sleep (30%)
  const sleepPts = (sleepScore / 100) * 30;

  // Heart Rate (20%)
  let hrPts = 10;
  if (restingHR >= 55 && restingHR <= 75) {
    hrPts = 20;
  } else if ((restingHR >= 76 && restingHR <= 100) || restingHR < 50) {
    hrPts = 10;
  }

  // SpO2 (20%)
  let spO2Pts = 5;
  if (spO2 >= 97) {
    spO2Pts = 20;
  } else if (spO2 >= 95) {
    spO2Pts = 15;
  } else {
    spO2Pts = 5;
  }

  const totalScore = Math.round(activityScore + sleepPts + hrPts + spO2Pts);

  return {
    score: totalScore,
    breakdown: {
      activity: { value: Math.round((activityScore / 30) * 100), points: activityScore },
      sleep: { value: sleepScore, points: sleepPts },
      heart: { value: restingHR, points: hrPts },
      spO2: { value: spO2, points: spO2Pts }
    }
  };
}

export default function Dashboard() {
  const users = db.prepare('SELECT * FROM users LIMIT 1').all() as any[];
  if (users.length === 0) {
    redirect('/onboarding/personal-details');
  }
  const user = users[0];

  const healthData = db.prepare('SELECT * FROM health_data WHERE user_id = ? ORDER BY recorded_at DESC LIMIT 1').get(user.id) as any;
  const currentSteps = healthData?.steps || 8420;
  const currentCalories = healthData?.calories || 580;

  const stepsGoal = user.daily_steps_goal || 10000;
  const caloriesGoal = user.daily_calories_goal || 2000;

  const stepsPercent = Math.min(Math.round((currentSteps / stepsGoal) * 100), 100);
  const caloriesPercent = Math.min(Math.round((currentCalories / caloriesGoal) * 100), 100);

  const spO2 = 98;
  const sleepScore = 84;
  const restingHR = 100;

  const wellness = calculateWellnessScore(
    currentSteps,
    stepsGoal,
    currentCalories,
    caloriesGoal,
    sleepScore,
    restingHR,
    spO2
  );

  const wellnessCircumference = 2 * Math.PI * 50; // 314.159
  const wellnessOffset = wellnessCircumference - (wellness.score / 100) * wellnessCircumference;

  return (
    <>
      <header className="sticky top-0 z-40 w-full bg-primary-container/90 backdrop-blur-md border-b border-border-subtle px-5 py-3.5 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full border border-border-subtle bg-primary flex items-center justify-center font-bold text-[18px] text-on-primary">
            {user.first_name[0]}{user.last_name ? user.last_name[0] : ''}
          </div>
          <div>
            <p className="text-[12px] font-medium tracking-wider text-on-surface-variant/80 uppercase">
              {new Date().toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}
            </p>
            <h1 className="text-[18px] font-semibold text-on-surface tracking-tight leading-snug">Good Morning, {user.first_name}</h1>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <ThemeToggle />
          <Link href="/profile" className="w-10 h-10 rounded-full bg-surface-container-high border border-border-subtle flex items-center justify-center text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest transition-all duration-200">
            <span className="material-symbols-outlined text-[20px]" style={{ fontVariationSettings: "'FILL' 0" }}>person</span>
          </Link>
        </div>
      </header>

      <main className="pt-4 pb-28">
        {/* Network Egress Monitor Banner */}
        <section className="px-5 mb-4">
          <div className="relative overflow-hidden rounded-xl border border-orange-500/20 dark:border-white/10 bg-[#f1f5f9] dark:bg-[#0f1013] py-2.5 px-3.5 flex items-center justify-between">
            <div 
              className="absolute inset-0 opacity-50 dark:opacity-100 pointer-events-none"
              style={{
                backgroundImage: 'linear-gradient(to right, rgba(249, 115, 22, 0.05) 1px, transparent 1px), linear-gradient(to bottom, rgba(249, 115, 22, 0.05) 1px, transparent 1px)',
                backgroundSize: '12px 12px'
              }}
            ></div>
            <div className="relative z-10 flex items-center gap-3">
              <div className="relative flex h-2.5 w-2.5 flex-shrink-0">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#f97316] opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-[#f97316]"></span>
              </div>
              <div className="flex flex-col">
                <span className="font-mono text-[10px] sm:text-xs font-semibold tracking-wider text-[#f97316] uppercase">
                  100% On-Device Edge Mode
                </span>
                <span className="font-mono text-[9px] sm:text-[11px] text-neutral-500 dark:text-neutral-400">
                  0 KB Egress to External Cloud
                </span>
              </div>
            </div>
            <div className="relative z-10 w-20 sm:w-28 h-8 ml-2 flex-shrink-0">
              <div className="absolute inset-0 border-l border-b border-orange-500/20">
                <div className="absolute top-1/3 left-0 right-0 border-t border-dashed border-orange-500/20"></div>
                <div className="absolute top-2/3 left-0 right-0 border-t border-dashed border-orange-500/20"></div>
                <div className="absolute top-0 bottom-0 left-1/3 border-l border-dashed border-orange-500/20"></div>
                <div className="absolute top-0 bottom-0 left-2/3 border-l border-dashed border-orange-500/20"></div>
              </div>
              <div className="absolute bottom-0 left-0 right-0 h-[1.5px] bg-[#f97316]"></div>
              <div className="absolute bottom-[-1.5px] left-0 w-6 h-[4.5px] bg-gradient-to-r from-transparent via-[#f97316] to-transparent"
                   style={{ animation: 'scanEgress 2.5s ease-in-out infinite' }}></div>
              <span className="absolute -top-1.5 right-0 font-mono text-[8px] sm:text-[9px] text-[#f97316]">0.0 KB/s</span>
            </div>
            <style dangerouslySetInnerHTML={{__html: `
              @keyframes scanEgress {
                0% { transform: translateX(-100%); opacity: 0; }
                15% { opacity: 1; }
                85% { opacity: 1; }
                100% { transform: translateX(500%); opacity: 0; }
              }
            `}} />
          </div>
        </section>

        <section className="px-5 mb-4">
          <div className="metric-group-glow rounded-2xl p-8 flex flex-col items-center justify-center gap-8 min-h-[320px]">
            <div className="relative w-48 h-48 flex items-center justify-center flex-shrink-0">
              <svg className="absolute inset-0 w-full h-full transform -rotate-90" viewBox="0 0 120 120">
                <defs>
                  <linearGradient id="scoreGradient" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#10B981" />
                    <stop offset="33%" stopColor="#A855F7" />
                    <stop offset="66%" stopColor="#EF4444" />
                    <stop offset="100%" stopColor="#F59E0B" />
                  </linearGradient>
                </defs>
                <circle cx="60" cy="60" fill="none" r="50" stroke="var(--border-subtle)" strokeWidth="6"></circle>
                <circle cx="60" cy="60" fill="none" r="50" stroke="url(#scoreGradient)" strokeDasharray={wellnessCircumference} strokeDashoffset={wellnessOffset} strokeLinecap="round" strokeWidth="8" className="transition-all duration-1000 ease-out"></circle>
              </svg>
              <div className="flex flex-col items-center text-center z-10 mt-1">
                <span className="text-6xl font-bold text-on-surface leading-none tracking-tight">{wellness.score}</span>
                <span className="text-[10px] text-neutral-400 font-bold tracking-widest mt-2 uppercase">DAILY RECOVERY</span>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-x-6 gap-y-6 w-full mt-2 pt-6 border-t border-border-subtle">
              {/* Activity */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <div className="w-1.5 h-1.5 rounded-full bg-[#10B981]"></div>
                    <span className="text-[12px] font-medium text-on-surface-variant uppercase tracking-wider">Activity</span>
                  </div>
                  <span className="text-[13px] font-bold text-on-surface">{wellness.breakdown.activity.value}%</span>
                </div>
                <div className="w-full h-1 bg-border-subtle rounded-full overflow-hidden">
                  <div className="h-full bg-[#10B981]" style={{ width: `${wellness.breakdown.activity.value}%` }}></div>
                </div>
              </div>

              {/* Sleep */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <div className="w-1.5 h-1.5 rounded-full bg-[#A855F7]"></div>
                    <span className="text-[12px] font-medium text-on-surface-variant uppercase tracking-wider">Sleep</span>
                  </div>
                  <span className="text-[13px] font-bold text-on-surface">{wellness.breakdown.sleep.value}</span>
                </div>
                <div className="w-full h-1 bg-border-subtle rounded-full overflow-hidden">
                  <div className="h-full bg-[#A855F7]" style={{ width: `${Math.min(wellness.breakdown.sleep.value, 100)}%` }}></div>
                </div>
              </div>

              {/* Heart */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <div className="w-1.5 h-1.5 rounded-full bg-[#EF4444]"></div>
                    <span className="text-[12px] font-medium text-on-surface-variant uppercase tracking-wider">Heart</span>
                  </div>
                  <span className="text-[13px] font-bold text-on-surface">{wellness.breakdown.heart.value} bpm</span>
                </div>
                <div className="w-full h-1 bg-border-subtle rounded-full overflow-hidden">
                  <div className="h-full bg-[#EF4444]" style={{ width: `${Math.min(Math.max((100 - Math.abs(wellness.breakdown.heart.value - 65) * 2), 0), 100)}%` }}></div>
                </div>
              </div>

              {/* SpO2 */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <div className="w-1.5 h-1.5 rounded-full bg-[#F59E0B]"></div>
                    <span className="text-[12px] font-medium text-on-surface-variant uppercase tracking-wider">SpO₂</span>
                  </div>
                  <span className="text-[13px] font-bold text-on-surface">{wellness.breakdown.spO2.value}%</span>
                </div>
                <div className="w-full h-1 bg-border-subtle rounded-full overflow-hidden">
                  <div className="h-full bg-[#F59E0B]" style={{ width: `${wellness.breakdown.spO2.value}%` }}></div>
                </div>
              </div>
            </div>
            <Link href="/health-report" className="w-full mt-2 flex items-center justify-center gap-1.5 bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 py-3 rounded-xl border border-rose-500/20 transition-all font-semibold text-sm group">
              Generate Report <span className="material-symbols-outlined text-[16px] group-hover:translate-x-1 transition-transform">arrow_forward</span>
            </Link>
          </div>
        </section>

        {/* Daily Activity Card */}
        <section className="px-5 mb-4">
          <div className="metric-group-glow rounded-2xl p-6 border border-border-subtle flex flex-col gap-4">
            <div className="flex justify-between items-center">
              <div className="flex items-center gap-2">
                <span className="material-symbols-outlined text-[#10B981] text-[20px]" style={{ fontVariationSettings: "'FILL' 1" }}>local_fire_department</span>
                <h3 className="text-[15px] font-semibold text-on-surface tracking-tight">Daily Activity</h3>
              </div>
              <span className="text-[12px] text-on-surface-variant font-medium">Active Burn & Movement</span>
            </div>

            <div className="grid grid-cols-2 gap-4">
              {/* Calories */}
              <div className="flex flex-col gap-1.5">
                <div className="flex items-center gap-1.5">
                  <div className="w-1.5 h-1.5 rounded-full bg-[#10B981]"></div>
                  <span className="text-[11px] font-semibold text-on-surface-variant uppercase tracking-wider">CALORIES</span>
                </div>
                <div className="flex items-baseline gap-1">
                  <span className="text-2xl font-bold text-on-surface leading-tight">{currentCalories.toLocaleString()}</span>
                  <span className="text-[12px] text-on-surface-variant font-medium">/ {caloriesGoal.toLocaleString()} kcal</span>
                </div>
                <div className="w-full h-[6px] bg-border-subtle rounded-full overflow-hidden mt-1">
                  <div className="h-full bg-[#10B981]" style={{ width: `${caloriesPercent}%` }}></div>
                </div>
              </div>

              {/* Steps */}
              <div className="flex flex-col gap-1.5 border-l border-border-subtle pl-4">
                <div className="flex items-center gap-1.5">
                  <div className="w-1.5 h-1.5 rounded-full bg-[#0EA5E9]"></div>
                  <span className="text-[11px] font-semibold text-on-surface-variant uppercase tracking-wider">STEPS</span>
                </div>
                <div className="flex items-baseline gap-1">
                  <span className="text-2xl font-bold text-on-surface leading-tight">{currentSteps.toLocaleString()}</span>
                  <span className="text-[12px] text-on-surface-variant font-medium">/ {stepsGoal.toLocaleString()}</span>
                </div>
                <div className="w-full h-[6px] bg-border-subtle rounded-full overflow-hidden mt-1">
                  <div className="h-full bg-[#0EA5E9]" style={{ width: `${stepsPercent}%` }}></div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="px-5 mb-4">
          <Link href="/blood-oxygen" className="block metric-group-glow rounded-xl p-6 relative overflow-hidden flex flex-col justify-between border border-border-subtle">
            <div className="flex justify-between items-center mb-1">
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ backgroundColor: 'rgba(245, 158, 11, 0.12)' }}>
                  <span className="material-symbols-outlined text-[20px]" style={{ color: '#f59e0b' }}>air</span>
                </div>
                <h2 className="text-base font-semibold text-on-surface tracking-tight">Blood Oxygen</h2>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[12px] font-semibold px-2.5 py-0.5 rounded-full whitespace-nowrap tracking-wide border" style={{ backgroundColor: 'rgba(34, 197, 94, 0.15)', color: '#22c55e', borderColor: 'rgba(34, 197, 94, 0.25)' }}>OPTIMAL</span>
                <span className="material-symbols-outlined text-on-surface-variant opacity-60 text-base">chevron_right</span>
              </div>
            </div>
            <div className="flex items-baseline gap-1.5 mt-3 mb-1">
              <span className="text-4xl font-bold tracking-tight text-on-surface">98</span>
              <span className="text-xl font-semibold" style={{ color: '#f59e0b' }}>%</span>
            </div>
            <p className="text-[12px] font-normal text-on-surface-variant leading-relaxed mb-2">Normal blood oxygen saturation (95–100%).</p>
            <div className="flex items-center gap-1.5 pt-2 border-t border-[rgba(255,255,255,0.05)]">
              <div className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: '#f59e0b' }}></div>
              <span className="text-[11px] font-normal text-on-surface-variant opacity-60">Updated just now</span>
            </div>
          </Link>
        </section>

        <section className="px-5 mb-4">
          <div className="grid grid-cols-2 gap-3">
            <Link href="/weather" className="metric-group-glow rounded-xl p-4 flex flex-col justify-between border border-border-subtle">
              <div className="flex items-center gap-2 mb-2">
                <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ backgroundColor: 'rgba(56, 189, 248, 0.12)' }}>
                  <span className="material-symbols-outlined text-[18px]" style={{ color: '#38bdf8' }}>wb_sunny</span>
                </div>
                <h4 className="text-[14px] font-semibold text-on-surface tracking-tight truncate">Weather & Heatwave</h4>
              </div>
              <p className="text-[12px] text-on-surface-variant leading-snug mb-3">38°C · Feels like 42°C · High heat risk in {user.location.split(',')[0]}</p>
              <div className="flex items-center gap-1 text-[12px] font-medium" style={{ color: '#38bdf8' }}>
                <span>View alerts</span>
                <span className="material-symbols-outlined text-[14px] leading-none">arrow_forward</span>
              </div>
            </Link>
            <Link href="/sleep" className="metric-group-glow rounded-xl p-4 flex flex-col justify-between border border-border-subtle">
              <div className="flex items-center gap-2 mb-2">
                <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ backgroundColor: 'rgba(168, 85, 247, 0.12)' }}>
                  <span className="material-symbols-outlined text-[18px]" style={{ color: '#a855f7' }}>bedtime</span>
                </div>
                <h4 className="text-[14px] font-semibold text-on-surface tracking-tight truncate">Sleep Insights</h4>
              </div>
              <p className="text-[12px] text-on-surface-variant leading-snug mb-3">Sleep score 84 · 7h 42m</p>
              <div className="flex items-center gap-1 text-[12px] font-medium" style={{ color: '#a855f7' }}>
                <span>View insights</span>
                <span className="material-symbols-outlined text-[14px] leading-none">arrow_forward</span>
              </div>
            </Link>
          </div>
        </section>

        <section className="px-5 mb-4">
          <BodyTemperatureCard />
        </section>

        <section className="px-5 mb-4">
          <Link href="/heart-rate" className="block metric-group-glow rounded-xl p-6 relative overflow-hidden min-h-[190px] flex flex-col justify-between border border-border-subtle">
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
                  <span className="material-symbols-outlined text-[#EF4444]" style={{ fontVariationSettings: "'FILL' 1" }}>favorite</span>
                  <h2 className="text-xl font-semibold text-on-surface tracking-tight">Heart Rate</h2>
                </div>
                <span className="bg-red-500/15 text-red-400 border border-red-500/30 text-[12px] font-bold px-3 py-1 rounded-full whitespace-nowrap tracking-wider uppercase">NORMAL</span>
              </div>
              <div className="flex items-baseline gap-2 mb-2">
                <span className="text-[48px] font-bold text-on-surface leading-none tracking-tight">100</span>
                <span className="text-[16px] font-semibold text-on-surface-variant tracking-wide">BPM</span>
              </div>
              <p className="text-[14px] font-normal text-on-surface-variant max-w-[80%] leading-relaxed">Resting heart rate in healthy range (60–100 BPM).</p>
            </div>
          </Link>
        </section>

        {/* Trends Section */}
        <section className="px-5 mb-8">
          <h3 className="text-xl font-bold text-on-surface mb-4 tracking-tight">Trends</h3>
          <HeartRateTrends />
        </section>
      </main>

      <nav className="fixed bottom-0 left-0 right-0 z-50 bg-primary-container/95 backdrop-blur-xl border-t border-border-subtle px-6 py-2.5 pb-[max(0.75rem,env(safe-area-inset-bottom))] flex justify-around items-center">
        <Link href="/dashboard" className="text-red-500 font-semibold bg-red-500/10 border border-red-500/20 rounded-xl px-3 py-1 flex flex-col items-center gap-1 transition-all duration-200">
          <span className="material-symbols-outlined text-[22px] leading-none text-red-500" style={{ fontVariationSettings: "'FILL' 1" }}>home</span>
          <span className="text-[11px] font-semibold tracking-normal whitespace-nowrap text-red-500">Home</span>
        </Link>
        <Link href="/weather" className="text-neutral-400 hover:text-neutral-200 font-medium flex flex-col items-center gap-1 px-3 py-1 transition-colors duration-200">
          <span className="material-symbols-outlined text-[22px] leading-none" style={{ fontVariationSettings: "'FILL' 0" }}>partly_cloudy_day</span>
          <span className="text-[11px] tracking-normal whitespace-nowrap">Weather</span>
        </Link>
        <Link href="/sleep" className="text-neutral-400 hover:text-neutral-200 font-medium flex flex-col items-center gap-1 px-3 py-1 transition-colors duration-200">
          <span className="material-symbols-outlined text-[22px] leading-none" style={{ fontVariationSettings: "'FILL' 0" }}>dark_mode</span>
          <span className="text-[11px] tracking-normal whitespace-nowrap">Sleep</span>
        </Link>
        <Link href="/profile" className="text-neutral-400 hover:text-neutral-200 font-medium flex flex-col items-center gap-1 px-3 py-1 transition-colors duration-200">
          <span className="material-symbols-outlined text-[22px] leading-none" style={{ fontVariationSettings: "'FILL' 0" }}>person</span>
          <span className="text-[11px] tracking-normal whitespace-nowrap">Profile</span>
        </Link>
      </nav>
    </>
  );
}
