import db from '@/lib/db';
import { redirect } from 'next/navigation';
import Link from 'next/link';

export default function Weatherindexpage() {
  const users = db.prepare('SELECT * FROM users LIMIT 1').all() as any[];
  if (users.length === 0) {
    redirect('/onboarding/personal-details');
  }
  const user = users[0];

  return (
    <>
      <header className="fixed top-0 left-0 right-0 z-40 w-full bg-[#0a0a0c]/90 backdrop-blur-md border-b border-white/[0.06] px-5 py-3.5 flex items-center justify-between transition-all duration-300">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full border border-white/10 bg-primary flex items-center justify-center font-bold text-[18px] text-on-primary">
            {user.first_name[0]}{user.last_name ? user.last_name[0] : ''}
          </div>
          <div className="flex flex-col">
            <span className="text-[11px] font-medium tracking-wider text-neutral-400 uppercase leading-none mb-1">
              {new Date().toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}
            </span>
            <h1 className="text-[18px] font-semibold text-white tracking-tight leading-tight">Good Morning, {user.first_name}</h1>
          </div>
        </div>
        <Link href="/profile" className="w-10 h-10 rounded-full bg-neutral-900/80 border border-white/10 flex items-center justify-center text-neutral-300 hover:text-white hover:bg-neutral-800/80 transition-all active:scale-95">
          <span className="material-symbols-outlined text-[20px]" style={{fontVariationSettings: "'FILL' 0"}}>person</span>
        </Link>
      </header>

      <main className="flex-1 px-5 pt-20 pb-28 space-y-7 max-w-5xl mx-auto w-full">
        {/* Top Environmental Conditions Card */}
        <div className="bg-neutral-900/80 border border-white/10 rounded-2xl p-5 shadow-xl backdrop-blur-md mt-4">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <span className="material-symbols-outlined text-[20px] text-emerald-400">location_on</span>
              <span className="text-base font-semibold text-white tracking-tight">{user.location.split(',')[0]}</span>
              <span className="bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-[10px] font-bold px-2 py-0.5 rounded-full tracking-wider uppercase flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>LIVE
              </span>
            </div>
            <span className="text-[11px] text-neutral-400">Updated 2 mins ago</span>
          </div>

          <div className="flex items-center justify-between py-2 border-b border-white/[0.08] pb-5">
            <div className="flex items-baseline gap-2">
              <span className="text-5xl font-bold tracking-tight text-white">38°C</span>
              <span className="text-sm text-neutral-400 font-medium">Feels like 42°C</span>
            </div>
            <div className="flex items-center gap-2 text-amber-400 bg-amber-400/10 border border-amber-400/20 px-3 py-1.5 rounded-xl">
              <span className="material-symbols-outlined text-[20px]">thermostat</span>
              <span className="text-sm font-semibold">Heatwave Warning</span>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 pt-4">
            <div className="bg-neutral-800/40 rounded-xl p-3 border border-white/[0.04] flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-lg bg-blue-500/10 border border-blue-500/20 flex items-center justify-center text-blue-400">
                  <span className="material-symbols-outlined text-[18px]">humidity_percentage</span>
                </div>
                <div>
                  <div className="text-[11px] text-neutral-400 uppercase tracking-wider">Humidity</div>
                  <div className="text-sm font-semibold text-white">64%</div>
                </div>
              </div>
            </div>

            <div className="bg-neutral-800/40 rounded-xl p-3 border border-white/[0.04] flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-lg bg-red-500/10 border border-red-500/20 flex items-center justify-center text-red-400">
                  <span className="material-symbols-outlined text-[18px]">wb_incandescent</span>
                </div>
                <div>
                  <div className="text-[11px] text-neutral-400 uppercase tracking-wider">UV Index</div>
                  <div className="text-sm font-semibold text-white">9 (Very High)</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* IMD Severe Weather Advisory Banner */}
        <div className="bg-red-950/30 border border-red-500/30 rounded-2xl p-4 relative overflow-hidden shadow-lg mt-4">
          <div className="flex items-start gap-3">
            <div className="w-9 h-9 rounded-xl bg-red-500/20 border border-red-500/40 flex items-center justify-center text-red-400 flex-shrink-0 mt-0.5">
              <span className="material-symbols-outlined text-[20px]">warning</span>
            </div>
            <div className="space-y-1.5 flex-1">
              <div className="flex items-center justify-between flex-wrap gap-1">
                <span className="text-[11px] font-bold tracking-wider text-red-400 uppercase">IMD SEVERE WEATHER ADVISORY • ACTIVE</span>
                <span className="text-[10px] text-red-400 font-medium bg-red-500/15 border border-red-500/30 px-2 py-0.5 rounded-full">Orange Alert</span>
              </div>
              <p className="text-xs text-neutral-300 leading-relaxed">
                Severe heatwave conditions persisting across Delhi-NCR. Peak afternoon temperatures expected to breach 44°C with elevated wet-bulb risk.
              </p>
              <div className="mt-2 pt-2 border-t border-red-500/20 flex items-center gap-2 text-[11px] text-red-300/90 font-medium">
                <span className="material-symbols-outlined text-[16px] text-amber-400">schedule</span>
                <span>Recommended safe outdoor window: Before 10:00 AM or after 6:30 PM.</span>
              </div>
            </div>
          </div>
        </div>

        {/* Air Quality Index (AQI) Card */}
        <div className="bg-neutral-900/80 border border-white/10 rounded-2xl p-5 backdrop-blur-md shadow-xl space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-base font-semibold text-white tracking-tight">Air Quality Index (AQI)</h2>
              <p className="text-[11px] text-neutral-400">Ambient Station Monitoring</p>
            </div>
            <span className="bg-red-500/15 border border-red-500/30 text-red-400 text-xs font-bold px-3 py-1 rounded-xl uppercase tracking-wider">
              AQI 218 • POOR
            </span>
          </div>

          <div className="space-y-1.5">
            <div className="w-full h-2 rounded-full bg-neutral-800 overflow-hidden flex">
              <div className="h-full bg-emerald-500 w-[20%] opacity-50"></div>
              <div className="h-full bg-yellow-500 w-[20%] opacity-50"></div>
              <div className="h-full bg-orange-500 w-[20%] opacity-50"></div>
              <div className="h-full bg-red-500 w-[20%] opacity-50"></div>
              <div className="h-full bg-purple-500 w-[20%] opacity-50"></div>
            </div>
            <div className="flex justify-between text-[10px] text-neutral-500 font-medium">
              <span>0 (Good)</span>
              <span>100 (Moderate)</span>
              <span>300 (Hazardous)</span>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-2">
            <div className="bg-neutral-800/50 border border-white/[0.05] p-2.5 rounded-xl text-center">
              <div className="text-[10px] text-neutral-400 uppercase font-semibold">PM2.5</div>
              <div className="text-sm font-bold text-neutral-300 mt-0.5">112 <span className="text-[10px] font-normal text-neutral-500">µg/m³</span></div>
            </div>
            <div className="bg-neutral-800/50 border border-white/[0.05] p-2.5 rounded-xl text-center">
              <div className="text-[10px] text-neutral-400 uppercase font-semibold">PM10</div>
              <div className="text-sm font-bold text-neutral-300 mt-0.5">194 <span className="text-[10px] font-normal text-neutral-500">µg/m³</span></div>
            </div>
            <div className="bg-neutral-800/50 border border-white/[0.05] p-2.5 rounded-xl text-center">
              <div className="text-[10px] text-neutral-400 uppercase font-semibold">Ozone (O₃)</div>
              <div className="text-sm font-bold text-neutral-300 mt-0.5">42 <span className="text-[10px] font-normal text-neutral-500">ppb</span></div>
            </div>
          </div>

          {/* Personalized Asthma Warning Profile */}
          <div className="bg-amber-500/10 border border-amber-500/20 rounded-xl p-3 flex items-start gap-2.5 mt-4">
            <span className="material-symbols-outlined text-[18px] text-amber-400 flex-shrink-0 mt-0.5">air</span>
            <div className="text-xs text-neutral-300 leading-relaxed">
              <span className="text-amber-300 font-semibold">Personalized Health Alert:</span> Moderate respiratory sensitivity detected. Keep prescribed inhaler handy and avoid strenuous outdoor exercise between 12 PM - 5 PM.
            </div>
          </div>
        </div>

        {/* Hourly & AQI Trend Chart */}
        <div className="bg-neutral-900/80 border border-white/10 rounded-2xl p-5 backdrop-blur-md shadow-xl space-y-4">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <div>
              <h2 className="text-base font-semibold text-white tracking-tight">Hourly & AQI Trend</h2>
              <p className="text-[11px] text-neutral-400">Next 6 Hours Forecast</p>
            </div>
            <div className="flex items-center gap-3 text-xs">
              <span className="flex items-center gap-1.5 text-amber-400 font-medium">
                <span className="w-2.5 h-0.5 bg-amber-400 rounded-full"></span> Temp (°C)
              </span>
              <span className="flex items-center gap-1.5 text-red-400 font-medium">
                <span className="w-2.5 h-0.5 bg-red-400 rounded-full"></span> AQI
              </span>
            </div>
          </div>

          <div className="w-full h-36 relative pt-2">
            <svg className="w-full h-full overflow-visible" viewBox="0 0 500 120" preserveAspectRatio="none">
              <defs>
                <linearGradient id="aqiGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#ef4444" stopOpacity="0.3"></stop>
                  <stop offset="100%" stopColor="#ef4444" stopOpacity="0.0"></stop>
                </linearGradient>
                <linearGradient id="tempGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#f59e0b" stopOpacity="0.25"></stop>
                  <stop offset="100%" stopColor="#f59e0b" stopOpacity="0.0"></stop>
                </linearGradient>
              </defs>

              <line x1="0" y1="20" x2="500" y2="20" stroke="rgba(255,255,255,0.06)" strokeDasharray="3 3"></line>
              <line x1="0" y1="60" x2="500" y2="60" stroke="rgba(255,255,255,0.06)" strokeDasharray="3 3"></line>
              <line x1="0" y1="100" x2="500" y2="100" stroke="rgba(255,255,255,0.06)" strokeDasharray="3 3"></line>

              <path d="M 20,40 Q 116,25 212,20 T 404,50 T 480,75 L 480,110 L 20,110 Z" fill="url(#aqiGrad)"></path>
              <path d="M 20,40 Q 116,25 212,20 T 404,50 T 480,75" fill="none" stroke="#ef4444" strokeWidth="2.5" strokeLinecap="round"></path>
              <path d="M 20,60 Q 116,40 212,35 T 404,65 T 480,85" fill="none" stroke="#f59e0b" strokeWidth="2.5" strokeLinecap="round" strokeDasharray="4 2"></path>

              <circle cx="20" cy="40" r="3.5" fill="#ef4444" stroke="#131313" strokeWidth="1.5"></circle>
              <circle cx="116" cy="28" r="3.5" fill="#ef4444" stroke="#131313" strokeWidth="1.5"></circle>
              <circle cx="212" cy="20" r="4.5" fill="#ef4444" stroke="#fff" strokeWidth="1.5"></circle>
              <circle cx="308" cy="32" r="3.5" fill="#ef4444" stroke="#131313" strokeWidth="1.5"></circle>
              <circle cx="404" cy="50" r="3.5" fill="#ef4444" stroke="#131313" strokeWidth="1.5"></circle>
              <circle cx="480" cy="75" r="3.5" fill="#ef4444" stroke="#131313" strokeWidth="1.5"></circle>
            </svg>
          </div>

          <div className="grid grid-cols-6 gap-1 text-center pt-1 border-t border-white/[0.06]">
            <div>
              <div className="text-[11px] font-medium text-white">1 PM</div>
              <div className="text-[10px] text-amber-400 font-semibold">37°C</div>
              <div className="text-[9px] text-red-400 font-medium">204 AQI</div>
            </div>
            <div>
              <div className="text-[11px] font-medium text-white">2 PM</div>
              <div className="text-[10px] text-amber-400 font-semibold">39°C</div>
              <div className="text-[9px] text-red-400 font-medium">218 AQI</div>
            </div>
            <div>
              <div className="text-[11px] font-bold text-white">3 PM</div>
              <div className="text-[10px] text-amber-400 font-bold">40°C</div>
              <div className="text-[9px] text-red-400 font-bold">232 AQI</div>
            </div>
            <div>
              <div className="text-[11px] font-medium text-white">4 PM</div>
              <div className="text-[10px] text-amber-400 font-semibold">39°C</div>
              <div className="text-[9px] text-red-400 font-medium">225 AQI</div>
            </div>
            <div>
              <div className="text-[11px] font-medium text-white">5 PM</div>
              <div className="text-[10px] text-amber-400 font-semibold">38°C</div>
              <div className="text-[9px] text-red-400 font-medium">210 AQI</div>
            </div>
            <div>
              <div className="text-[11px] font-medium text-white">6 PM</div>
              <div className="text-[10px] text-amber-400 font-semibold">36°C</div>
              <div className="text-[9px] text-red-400 font-medium">195 AQI</div>
            </div>
          </div>
        </div>

        {/* Heat Risk Index Card */}
        <div className="bg-neutral-900/80 border border-white/10 rounded-2xl p-5 backdrop-blur-md shadow-xl space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <span className="text-[11px] font-bold tracking-wider text-neutral-400 uppercase">HEAT RISK INDEX</span>
              <h2 className="text-base font-semibold text-white tracking-tight">Physiological Burden</h2>
            </div>
            <span className="bg-red-500/15 border border-red-500/30 text-red-400 font-bold text-xs px-2.5 py-1 rounded-xl uppercase tracking-wider">
              82 / 100 HIGH RISK
            </span>
          </div>

          <div className="flex flex-col sm:flex-row items-center gap-5 pt-1">
            <div className="relative w-28 h-28 flex-shrink-0 flex items-center justify-center">
              <svg className="w-full h-full -rotate-90" viewBox="0 0 100 100">
                <circle cx="50" cy="50" r="40" fill="none" stroke="#2a2a2a" strokeWidth="9"></circle>
                <circle cx="50" cy="50" r="40" fill="none" stroke="#ef4444" strokeWidth="9" strokeDasharray="251.2" strokeDashoffset="45.2" strokeLinecap="round"></circle>
              </svg>
              <div className="absolute flex flex-col items-center justify-center">
                <span className="text-2xl font-bold text-white tracking-tight">82</span>
                <span className="text-[10px] font-bold uppercase tracking-wider text-neutral-400">RISK</span>
              </div>
            </div>

            <div className="flex-1 w-full space-y-2.5 text-xs">
              <div className="flex justify-between items-center pb-1.5 border-b border-white/[0.06]">
                <span className="text-neutral-400">Heat Index</span>
                <span className="text-white font-semibold">44°C</span>
              </div>
              <div className="flex justify-between items-center pb-1.5 border-b border-white/[0.06]">
                <span className="text-neutral-400">Burden Humidity</span>
                <span className="text-white font-semibold">64%</span>
              </div>
              <div className="flex justify-between items-center pb-1.5 border-b border-white/[0.06]">
                <span className="text-neutral-400">Amplification Direct Solar</span>
                <span className="text-white font-semibold">High (9.2 kW/m²)</span>
              </div>
              <div className="flex justify-between items-center pb-1.5 border-b border-white/[0.06]">
                <span className="text-neutral-400">Radiation</span>
                <span className="text-white font-semibold">UV 9.4</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-neutral-400">Ambient Air Circulation</span>
                <span className="text-white font-semibold">Calm (6 km/h)</span>
              </div>
            </div>
          </div>
        </div>
        <div className="space-y-3 pb-6">
          <div className="flex flex-col">
            <h2 className="text-lg font-semibold text-white tracking-tight">Heatwave Protocols</h2>
            <p className="text-xs text-neutral-400">Clinical & lifestyle precautions for severe heatwave conditions.</p>
          </div>

          <div className="space-y-2.5">
            <div className="bg-neutral-900/80 border border-white/10 rounded-2xl p-3.5 flex items-start gap-3 backdrop-blur-md">
              <div className="w-9 h-9 rounded-xl bg-[#2B73F6]/10 border border-[#2B73F6]/20 flex items-center justify-center text-[#2B73F6] flex-shrink-0 mt-0.5">
                <span className="material-symbols-outlined text-[20px]">water_drop</span>
              </div>
              <div>
                <h3 className="text-sm font-semibold text-white">Hydrate Regularly</h3>
                <p className="text-xs text-neutral-400 mt-0.5 leading-relaxed">Target 3.5L today with mineral electrolytes to counter heavy transpiration.</p>
              </div>
            </div>

            <div className="bg-neutral-900/80 border border-white/10 rounded-2xl p-3.5 flex items-start gap-3 backdrop-blur-md">
              <div className="w-9 h-9 rounded-xl bg-[#2B73F6]/10 border border-[#2B73F6]/20 flex items-center justify-center text-[#2B73F6] flex-shrink-0 mt-0.5">
                <span className="material-symbols-outlined text-[20px]">checkroom</span>
              </div>
              <div>
                <h3 className="text-sm font-semibold text-white">Light Clothing</h3>
                <p className="text-xs text-neutral-400 mt-0.5 leading-relaxed">Wear loose-fitting, light-colored cotton or UV-protective fabrics outdoors.</p>
              </div>
            </div>

            <div className="bg-neutral-900/80 border border-white/10 rounded-2xl p-3.5 flex items-start gap-3 backdrop-blur-md">
              <div className="w-9 h-9 rounded-xl bg-[#2B73F6]/10 border border-[#2B73F6]/20 flex items-center justify-center text-[#2B73F6] flex-shrink-0 mt-0.5">
                <span className="material-symbols-outlined text-[20px]">ac_unit</span>
              </div>
              <div>
                <h3 className="text-sm font-semibold text-white">Shade & Cooling</h3>
                <p className="text-xs text-neutral-400 mt-0.5 leading-relaxed">Stay in air-conditioned or well-ventilated spaces during peak sun hours.</p>
              </div>
            </div>

            <div className="bg-neutral-900/80 border border-white/10 rounded-2xl p-3.5 flex items-start gap-3 backdrop-blur-md">
              <div className="w-9 h-9 rounded-xl bg-[#2B73F6]/10 border border-[#2B73F6]/20 flex items-center justify-center text-[#2B73F6] flex-shrink-0 mt-0.5">
                <span className="material-symbols-outlined text-[20px]">vital_signs</span>
              </div>
              <div>
                <h3 className="text-sm font-semibold text-white">Symptom Watch</h3>
                <p className="text-xs text-neutral-400 mt-0.5 leading-relaxed">Monitor for early warning signs: dizziness, nausea, throbbing headache, or cramps.</p>
              </div>
            </div>

            <div className="bg-neutral-900/80 border border-white/10 rounded-2xl p-3.5 flex items-start gap-3 backdrop-blur-md">
              <div className="w-9 h-9 rounded-xl bg-[#2B73F6]/10 border border-[#2B73F6]/20 flex items-center justify-center text-[#2B73F6] flex-shrink-0 mt-0.5">
                <span className="material-symbols-outlined text-[20px]">pets</span>
              </div>
              <div>
                <h3 className="text-sm font-semibold text-white">Vulnerables & Pets</h3>
                <p className="text-xs text-neutral-400 mt-0.5 leading-relaxed">Ensure elderly family members, young children, and pets stay cool and hydrated.</p>
              </div>
            </div>
          </div>
        </div>
      </main>

      <nav className="fixed bottom-0 left-0 right-0 z-50 bg-[#0a0a0c]/95 backdrop-blur-xl border-t border-white/[0.08] px-6 py-2.5 pb-[max(0.75rem,env(safe-area-inset-bottom))] flex justify-around items-center">
        <Link href="/dashboard" className="text-neutral-400 hover:text-neutral-200 font-medium flex flex-col items-center gap-1 px-3 py-1 transition-colors duration-200">
          <span className="material-symbols-outlined text-[22px] leading-none" style={{fontVariationSettings: "'FILL' 0"}}>home</span>
          <span className="text-[11px] tracking-normal whitespace-nowrap">Home</span>
        </Link>
        <Link href="/weather" className="text-emerald-400 font-semibold bg-emerald-500/10 rounded-xl px-3 py-1 flex flex-col items-center gap-1 transition-all duration-200">
          <span className="material-symbols-outlined text-[22px] leading-none" style={{fontVariationSettings: "'FILL' 1"}}>partly_cloudy_day</span>
          <span className="text-[11px] font-semibold tracking-normal whitespace-nowrap">Weather</span>
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
