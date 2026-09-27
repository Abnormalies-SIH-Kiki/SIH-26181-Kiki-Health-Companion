"use client";

import Link from 'next/link';
import { useEffect } from 'react';

export default function HealthReportPage() {
  
  useEffect(() => {
    // Add any necessary client-side init here
  }, []);

  return (
    <div className="min-h-screen bg-[#0f1013] print:bg-white text-on-surface print:text-[#0f172a] font-sans pb-20 print:pb-0">
      
      {/* Print Styles inline for convenience */}
      <style dangerouslySetInnerHTML={{__html: `
        @media print {
          @page { margin: 12mm; size: A4 portrait; }
          .no-print { display: none !important; }
          body, html { background: white !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
          .print-card { 
            background: white !important; 
            border: 1px solid #e2e8f0 !important; 
            color: #0f172a !important;
            box-shadow: none !important;
          }
          .print-text { color: #0f172a !important; }
          .print-text-muted { color: #64748b !important; }
          .print-border { border-color: #e2e8f0 !important; }
        }
      `}} />

      {/* 1. Sticky Top Action Bar (Non-Printable) */}
      <header className="no-print sticky top-0 z-50 w-full bg-[#0f1013]/90 backdrop-blur-md border-b border-white/5 px-5 py-3.5 flex items-center justify-between">
        <Link href="/profile" className="flex items-center gap-1.5 text-neutral-400 hover:text-white transition-colors">
          <span className="material-symbols-outlined text-[20px]">arrow_back</span>
          <span className="text-[13px] font-medium tracking-wide">Back to Profile</span>
        </Link>
        <h1 className="text-[15px] font-semibold text-white tracking-tight hidden sm:block">Health Summary Report</h1>
        <button 
          onClick={() => window.print()}
          className="flex items-center gap-1.5 bg-cyan-600 hover:bg-cyan-500 text-white px-3 py-1.5 rounded-lg text-[13px] font-medium shadow-[0_0_10px_rgba(8,145,178,0.3)] transition-colors"
        >
          <span className="material-symbols-outlined text-[18px]">download</span>
          <span>Download PDF</span>
        </button>
      </header>

      {/* 2. Printable Report Document Container */}
      <main className="px-5 pt-6 max-w-3xl mx-auto print:px-0 print:pt-0">
        
        {/* Document Header */}
        <div className="flex flex-col gap-4 mb-8 print:mb-6">
          <div className="flex justify-between items-start">
            <div>
              <div className="flex items-center gap-2 mb-1">
                <span className="material-symbols-outlined text-cyan-500 print:text-cyan-600 text-[24px]">local_hospital</span>
                <h2 className="text-xl font-bold tracking-tight text-white print-text">KIKI Health</h2>
              </div>
              <p className="text-sm font-medium tracking-wider text-cyan-400 print:text-cyan-600 uppercase">Confidential Health Summary</p>
            </div>
            <div className="text-right">
              <p className="text-xs font-mono text-neutral-400 print-text-muted">{new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</p>
              <div className="mt-2 inline-flex items-center gap-1 bg-emerald-500/10 print:bg-emerald-50 border border-emerald-500/20 print:border-emerald-200 px-2 py-0.5 rounded text-[10px] font-mono text-emerald-400 print:text-emerald-600 uppercase">
                <span className="material-symbols-outlined text-[12px]">verified_user</span>
                Processed 100% On-Device
              </div>
            </div>
          </div>

          {/* Patient Information Strip */}
          <div className="bg-[#1a1b1e] print-card rounded-xl border border-white/5 p-4 flex flex-col sm:flex-row justify-between gap-4 mt-2">
            <div>
              <p className="text-[10px] text-neutral-500 print-text-muted uppercase font-bold tracking-wider mb-0.5">Patient Name</p>
              <p className="text-base font-semibold text-white print-text">Aniket Sharma</p>
            </div>
            <div className="h-px w-full sm:w-px sm:h-auto bg-white/5 print-border"></div>
            <div>
              <p className="text-[10px] text-neutral-500 print-text-muted uppercase font-bold tracking-wider mb-0.5">Demographics</p>
              <p className="text-sm font-medium text-white print-text mt-1">28 yrs &bull; Man</p>
            </div>
            <div className="h-px w-full sm:w-px sm:h-auto bg-white/5 print-border"></div>
            <div>
              <p className="text-[10px] text-neutral-500 print-text-muted uppercase font-bold tracking-wider mb-0.5">Vitals</p>
              <p className="text-sm font-medium text-neutral-300 print:text-neutral-700 mt-1">
                H: 185 cm <span className="mx-1 text-white/20 print:text-neutral-300">|</span> W: 70 kg <span className="mx-1 text-white/20 print:text-neutral-300">|</span> O+
              </p>
            </div>
          </div>
        </div>

        {/* Executive Scorecard */}
        <div className="bg-[#1a1b1e] print-card rounded-2xl border border-white/5 p-5 mb-6">
          <div className="flex items-center justify-between mb-5">
            <h3 className="text-sm font-bold text-white print-text tracking-tight uppercase">Executive Wellness Score</h3>
            <div className="bg-cyan-500/10 print:bg-cyan-50 border border-cyan-500/20 print:border-cyan-200 px-3 py-1 rounded-full flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-cyan-400 print:bg-cyan-500"></span>
              <span className="text-xs font-bold text-cyan-400 print:text-cyan-600 tracking-wide">93 / 100 - OPTIMAL</span>
            </div>
          </div>
          
          <div className="grid grid-cols-4 gap-3">
            {/* Chips */}
            <div className="bg-black/20 print:bg-neutral-50 rounded-lg p-3 border border-white/5 print-border text-center">
              <span className="material-symbols-outlined text-emerald-400 print:text-emerald-600 text-[20px] mb-1">local_fire_department</span>
              <p className="text-[10px] font-bold text-neutral-400 print-text-muted uppercase tracking-wider">Activity</p>
              <p className="text-sm font-bold text-white print-text">92%</p>
            </div>
            <div className="bg-black/20 print:bg-neutral-50 rounded-lg p-3 border border-white/5 print-border text-center">
              <span className="material-symbols-outlined text-purple-400 print:text-purple-600 text-[20px] mb-1">bedtime</span>
              <p className="text-[10px] font-bold text-neutral-400 print-text-muted uppercase tracking-wider">Sleep</p>
              <p className="text-sm font-bold text-white print-text">84</p>
            </div>
            <div className="bg-black/20 print:bg-neutral-50 rounded-lg p-3 border border-white/5 print-border text-center">
              <span className="material-symbols-outlined text-red-400 print:text-red-600 text-[20px] mb-1">favorite</span>
              <p className="text-[10px] font-bold text-neutral-400 print-text-muted uppercase tracking-wider">Heart</p>
              <p className="text-sm font-bold text-white print-text">100 bpm</p>
            </div>
            <div className="bg-black/20 print:bg-neutral-50 rounded-lg p-3 border border-white/5 print-border text-center">
              <span className="material-symbols-outlined text-amber-400 print:text-amber-500 text-[20px] mb-1">air</span>
              <p className="text-[10px] font-bold text-neutral-400 print-text-muted uppercase tracking-wider">SpO2</p>
              <p className="text-sm font-bold text-white print-text">98%</p>
            </div>
          </div>
        </div>

        {/* Detailed Health Sections */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
          
          {/* Heart Health */}
          <div className="bg-[#1a1b1e] print-card rounded-xl border border-white/5 p-5">
            <div className="flex items-center gap-2 mb-3">
              <span className="material-symbols-outlined text-red-500 text-[18px]">favorite</span>
              <h4 className="text-sm font-bold text-white print-text uppercase tracking-wider">Heart Health</h4>
            </div>
            <div className="mb-4">
              <p className="text-2xl font-bold text-white print-text">100 <span className="text-sm font-medium text-neutral-500 print-text-muted">BPM</span></p>
              <p className="text-[11px] text-neutral-400 print-text-muted mt-1">Resting Average (Normal 60-100 BPM)</p>
            </div>
            <div className="bg-emerald-500/10 print:bg-emerald-50 border border-emerald-500/20 print:border-emerald-200 p-2.5 rounded text-xs text-emerald-400 print:text-emerald-700 font-medium">
              No critical high/low heart rate events detected in the last 24h.
            </div>
            <div className="mt-4 h-12 w-full flex items-end gap-1 opacity-80 print:opacity-100">
              {/* Fake ECG trend */}
              {[40, 50, 45, 60, 55, 70, 65, 50, 55, 45, 50, 40].map((h, i) => (
                <div key={i} className="flex-1 bg-red-500/40 print:bg-red-200 rounded-t-sm" style={{ height: `${h}%` }}></div>
              ))}
            </div>
          </div>

          {/* Sleep Insights */}
          <div className="bg-[#1a1b1e] print-card rounded-xl border border-white/5 p-5">
            <div className="flex items-center gap-2 mb-3">
              <span className="material-symbols-outlined text-purple-500 text-[18px]">bedtime</span>
              <h4 className="text-sm font-bold text-white print-text uppercase tracking-wider">Sleep Insights</h4>
            </div>
            <div className="flex justify-between items-end mb-4">
              <div>
                <p className="text-2xl font-bold text-white print-text">84<span className="text-sm font-medium text-neutral-500 print-text-muted">/100</span></p>
                <p className="text-[11px] text-neutral-400 print-text-muted mt-1">Sleep Score</p>
              </div>
              <div className="text-right">
                <p className="text-lg font-bold text-white print-text">7h 42m</p>
                <p className="text-[11px] text-neutral-400 print-text-muted mt-1">Total Duration</p>
              </div>
            </div>
            <div className="mt-4">
              <div className="w-full h-3 rounded-full flex overflow-hidden border border-white/10 print:border-neutral-200">
                <div className="bg-purple-600 h-full print:border-r print:border-white" style={{ width: '20%' }} title="Deep"></div>
                <div className="bg-purple-400 h-full print:border-r print:border-white" style={{ width: '50%' }} title="Light"></div>
                <div className="bg-cyan-400 h-full print:border-r print:border-white" style={{ width: '25%' }} title="REM"></div>
                <div className="bg-amber-500 h-full" style={{ width: '5%' }} title="Awake"></div>
              </div>
              <div className="flex justify-between text-[9px] font-bold text-neutral-500 print-text-muted uppercase mt-2 px-1">
                <span>Deep</span>
                <span>Light</span>
                <span>REM</span>
                <span>Awake</span>
              </div>
            </div>
          </div>

          {/* Blood Oxygen */}
          <div className="bg-[#1a1b1e] print-card rounded-xl border border-white/5 p-5">
            <div className="flex items-center gap-2 mb-3">
              <span className="material-symbols-outlined text-amber-500 text-[18px]">air</span>
              <h4 className="text-sm font-bold text-white print-text uppercase tracking-wider">Blood Oxygen (SpO2)</h4>
            </div>
            <div className="mb-4">
              <p className="text-2xl font-bold text-white print-text">98%</p>
              <p className="text-[11px] text-neutral-400 print-text-muted mt-1">Optimal saturation range (95-100%)</p>
            </div>
            <div className="w-full h-1 bg-white/5 print:bg-neutral-100 rounded-full overflow-hidden mt-6 relative">
              <div className="absolute left-[95%] top-0 bottom-0 w-[2px] bg-red-500 z-10"></div>
              <div className="h-full bg-gradient-to-r from-amber-600 to-amber-400 print:bg-amber-400" style={{ width: '98%' }}></div>
            </div>
            <p className="text-[10px] text-neutral-500 print-text-muted mt-2">Consistent stable levels throughout the day.</p>
          </div>

          {/* Physical Activity */}
          <div className="bg-[#1a1b1e] print-card rounded-xl border border-white/5 p-5">
            <div className="flex items-center gap-2 mb-3">
              <span className="material-symbols-outlined text-emerald-500 text-[18px]">directions_run</span>
              <h4 className="text-sm font-bold text-white print-text uppercase tracking-wider">Physical Activity</h4>
            </div>
            
            <div className="space-y-4">
              <div>
                <div className="flex justify-between items-baseline mb-1">
                  <p className="text-[11px] font-bold text-neutral-400 print-text-muted uppercase tracking-wider">Steps</p>
                  <p className="text-sm font-bold text-white print-text">8,420 <span className="text-[10px] font-normal text-neutral-500 print-text-muted">/ 10k (84%)</span></p>
                </div>
                <div className="w-full h-1.5 bg-white/5 print:bg-neutral-100 rounded-full overflow-hidden">
                  <div className="h-full bg-cyan-500 print:bg-cyan-600" style={{ width: '84%' }}></div>
                </div>
              </div>

              <div>
                <div className="flex justify-between items-baseline mb-1">
                  <p className="text-[11px] font-bold text-neutral-400 print-text-muted uppercase tracking-wider">Calories</p>
                  <p className="text-sm font-bold text-white print-text">580 <span className="text-[10px] font-normal text-neutral-500 print-text-muted">/ 500 kcal (100%)</span></p>
                </div>
                <div className="w-full h-1.5 bg-white/5 print:bg-neutral-100 rounded-full overflow-hidden">
                  <div className="h-full bg-emerald-500 print:bg-emerald-600" style={{ width: '100%' }}></div>
                </div>
              </div>
            </div>
          </div>

        </div>

        {/* Medical Disclaimer Footer */}
        <div className="border-t border-white/10 print-border pt-6 pb-12 print:pb-0">
          <p className="text-[10px] leading-relaxed text-neutral-500 print-text-muted text-justify">
            <strong>DISCLAIMER:</strong> This summary is generated for personal wellness tracking and informational purposes only. It is not intended for diagnostic use, treatment, or as a substitute for professional medical advice. Always consult with a qualified healthcare provider regarding any medical conditions or before making changes to your health regimen. Data processed 100% on-device for maximum privacy.
          </p>
        </div>

      </main>
    </div>
  );
}
