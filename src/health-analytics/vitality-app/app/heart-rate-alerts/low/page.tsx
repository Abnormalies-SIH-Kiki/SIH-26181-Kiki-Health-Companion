'use client';

import React, { useState } from 'react';
import Link from 'next/link';

interface Symptom {
  id: string;
  name: string;
  icon: string;
}

const LOW_SYMPTOMS: Symptom[] = [
  { id: 'dizziness', name: 'Dizziness', icon: 'sync_problem' },
  { id: 'fatigue', name: 'Fatigue', icon: 'bedtime' },
  { id: 'faint', name: 'Faint feeling', icon: 'blur_on' },
  { id: 'breath', name: 'Short of breath', icon: 'air' },
  { id: 'confusion', name: 'Confusion', icon: 'psychology_alt' },
  { id: 'weakness', name: 'Weakness', icon: 'accessibility_new' },
  { id: 'chest', name: 'Chest pressure', icon: 'vital_signs' },
  { id: 'other', name: 'Other sensation', icon: 'more_horiz' },
];

export default function LowHeartRateAlert() {
  const [selectedSymptoms, setSelectedSymptoms] = useState<string[]>([]);
  const [isLogged, setIsLogged] = useState(false);

  const toggleSymptom = (id: string) => {
    setSelectedSymptoms((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]
    );
  };

  const handleLog = () => {
    setIsLogged(true);
    setTimeout(() => {
      setIsLogged(false);
    }, 2500);
  };

  const handleFeelFine = () => {
    setSelectedSymptoms([]);
    setIsLogged(true);
    setTimeout(() => {
      setIsLogged(false);
    }, 2500);
  };

  return (
    <div className="min-h-screen bg-[#070708] text-white flex justify-center selection:bg-zinc-800 pb-safe">
      <div className="w-full max-w-md min-h-screen flex flex-col relative px-4 pb-12">
        {/* Top Header */}
        <header className="sticky top-0 w-full z-50 pt-safe bg-[#070708]/90 backdrop-blur-xl border-b border-white/5 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link
              href="/heart-rate-alerts"
              aria-label="Go back"
              className="w-10 h-10 -ml-1 rounded-full flex items-center justify-center text-zinc-300 hover:text-white hover:bg-zinc-800 transition-colors"
            >
              <span className="material-symbols-outlined text-[22px]">arrow_back</span>
            </Link>
            <h1 className="font-bold text-lg text-zinc-100 tracking-tight">Health Assessment</h1>
          </div>
          <div className="flex items-center gap-2">
            <Link
              href="/heart-rate-alerts"
              aria-label="Close flow"
              className="w-10 h-10 rounded-full flex items-center justify-center text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800 transition-colors"
            >
              <span className="material-symbols-outlined text-[20px]">close</span>
            </Link>
            <Link
              href="/profile"
              className="w-8 h-8 rounded-full bg-zinc-800 border border-white/10 flex items-center justify-center text-zinc-400 hover:text-white transition-colors"
            >
              <span className="material-symbols-outlined text-[18px]">person</span>
            </Link>
          </div>
        </header>

        {/* Main Content Area */}
        <main className="flex-1 w-full pt-4 flex flex-col space-y-4">
          {/* Biometric Reading Summary Banner */}
          <section className="w-full bg-[#1C1C1E] rounded-2xl p-5 flex flex-col gap-4 shadow-md relative overflow-hidden border border-white/5">
            <div className="absolute -right-8 -top-8 w-36 h-36 bg-[#53e16f]/10 rounded-full blur-3xl pointer-events-none"></div>
            
            <div className="flex items-center justify-between relative z-10">
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-full bg-zinc-900 flex items-center justify-center text-[#53e16f]">
                  <span className="material-symbols-outlined text-[18px]" style={{ fontVariationSettings: "'FILL' 1" }}>
                    favorite
                  </span>
                </div>
                <span className="font-bold text-[11px] uppercase tracking-wider text-zinc-400">HEART RATE EVENT</span>
              </div>
              <div className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-[#53e16f]/10 border border-[#53e16f]/25">
                <span className="w-2 h-2 rounded-full bg-[#53e16f] animate-pulse"></span>
                <span className="font-bold text-[11px] uppercase tracking-wider text-[#53e16f]">LOW · BELOW BASELINE</span>
              </div>
            </div>

            <div className="flex items-baseline justify-between pt-1 relative z-10">
              <div className="flex items-baseline gap-2">
                <span className="font-bold tabular-nums text-[40px] leading-none text-white tracking-tight">48</span>
                <span className="uppercase font-semibold text-[13px] text-zinc-400 tracking-wider">BPM</span>
              </div>
              <div className="text-right flex flex-col items-end">
                <span className="text-xs font-medium text-zinc-400 tracking-wider">MEASURED 2:45 PM</span>
              </div>
            </div>

            {/* Baseline comparison bar */}
            <div className="w-full bg-zinc-900 h-2.5 rounded-full overflow-hidden flex relative z-10 border border-white/5">
              <div className="bg-[#53e16f] h-full w-[28%] rounded-full shadow-[0_0_8px_rgba(83,225,111,0.4)]"></div>
              <div className="bg-zinc-800 h-full w-[72%]"></div>
            </div>

            <div className="flex justify-between items-center text-xs font-medium relative z-10">
              <span className="text-zinc-400">Baseline: 55-80 BPM</span>
              <span className="text-[#53e16f]">Current: -14% low</span>
            </div>
          </section>

          {/* Guided Query Section */}
          <section className="flex flex-col gap-1 px-1">
            <h2 className="font-bold text-2xl tracking-tight text-white">How are you feeling?</h2>
            <p className="font-normal text-sm leading-relaxed text-zinc-400">
              Your heart rate is resting lower than your typical rhythm. Choose any symptoms you may be feeling right now.
            </p>
          </section>

          {/* Symptoms Selectable Grid */}
          <section className="flex flex-col gap-2.5">
            <div className="flex items-center justify-between px-1">
              <span className="font-bold text-[11px] uppercase tracking-wider text-zinc-400">REPORTED SENSATIONS</span>
              <span className="font-semibold text-[11px] uppercase tracking-wider text-[#53e16f]">
                {selectedSymptoms.length} selected
              </span>
            </div>

            <div className="grid grid-cols-2 gap-3">
              {LOW_SYMPTOMS.map((symptom) => {
                const isSelected = selectedSymptoms.includes(symptom.id);
                return (
                  <button
                    key={symptom.id}
                    onClick={() => toggleSymptom(symptom.id)}
                    type="button"
                    className={`text-left p-4 rounded-xl border transition-all flex flex-col justify-between gap-3 min-h-[96px] group active:scale-[0.98] ${
                      isSelected
                        ? 'bg-[#2a2a2a] border-[#53e16f]/40 shadow-md scale-[1.01]'
                        : 'bg-[#1C1C1E] border-white/5 hover:bg-zinc-800'
                    }`}
                  >
                    <div className="flex items-center justify-between w-full">
                      <div
                        className={`w-9 h-9 rounded-full flex items-center justify-center transition-colors ${
                          isSelected ? 'bg-[#53e16f] text-black font-bold' : 'bg-zinc-900 text-zinc-400'
                        }`}
                      >
                        <span className="material-symbols-outlined text-[20px]">{symptom.icon}</span>
                      </div>
                      <span
                        className={`material-symbols-outlined text-[20px] transition-colors ${
                          isSelected ? 'text-[#53e16f]' : 'text-zinc-700'
                        }`}
                        style={{ fontVariationSettings: isSelected ? "'FILL' 1" : "'FILL' 0" }}
                      >
                        check_circle
                      </span>
                    </div>
                    <span className="font-semibold text-sm text-zinc-200 leading-tight">{symptom.name}</span>
                  </button>
                );
              })}
            </div>
          </section>

          {/* Clinical Emergency Advisory */}
          <section className="w-full bg-[#1C1C1E] rounded-xl p-4 flex gap-3.5 items-start border border-white/5">
            <div className="w-8 h-8 rounded-full bg-rose-950/40 border border-rose-500/20 flex items-center justify-center flex-shrink-0 text-rose-500 mt-0.5">
              <span className="material-symbols-outlined text-[18px]" style={{ fontVariationSettings: "'FILL' 1" }}>
                warning
              </span>
            </div>
            <div className="flex flex-col gap-1">
              <h3 className="font-semibold text-sm text-white tracking-tight">Emergency Caution</h3>
              <p className="font-normal text-xs leading-relaxed text-zinc-400">
                If you faint, experience acute chest pain, or have severe difficulty breathing, please seek immediate emergency care.
              </p>
            </div>
          </section>

          {/* Primary Interactive Controls */}
          <section className="flex flex-col gap-2.5 pt-2 mt-auto">
            <button
              onClick={handleLog}
              className="w-full h-14 rounded-full bg-zinc-100 text-zinc-950 font-semibold text-sm tracking-wide flex items-center justify-center gap-2 hover:bg-white transition-all active:scale-[0.98] shadow-lg shadow-black/40"
              type="button"
            >
              {isLogged ? (
                <>
                  <span className="material-symbols-outlined text-[20px] text-emerald-600">check_circle</span>
                  <span>Logged ({selectedSymptoms.length > 0 ? `${selectedSymptoms.length} Symptoms` : 'No Symptoms'})</span>
                </>
              ) : (
                <>
                  <span>
                    {selectedSymptoms.length > 0
                      ? `Log ${selectedSymptoms.length} Symptom${selectedSymptoms.length > 1 ? 's' : ''}`
                      : 'Log Selected Symptoms'}
                  </span>
                  <span className="material-symbols-outlined text-[20px]">arrow_forward</span>
                </>
              )}
            </button>
            <button
              onClick={handleFeelFine}
              className="w-full h-12 rounded-full bg-[#1C1C1E] text-zinc-300 font-semibold text-sm flex items-center justify-center gap-2 hover:bg-zinc-800 border border-white/5 transition-all active:scale-[0.98]"
              type="button"
            >
              <span className="material-symbols-outlined text-[19px] text-[#53e16f]">sentiment_satisfied</span>
              <span>I feel fine / No symptoms</span>
            </button>
          </section>
        </main>
      </div>
    </div>
  );
}
