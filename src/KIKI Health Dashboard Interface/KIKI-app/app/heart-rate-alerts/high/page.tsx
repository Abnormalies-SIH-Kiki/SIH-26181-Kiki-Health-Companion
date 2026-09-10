'use client';

import React, { useState } from 'react';
import Link from 'next/link';

interface Symptom {
  id: string;
  name: string;
  icon: string;
}

const HIGH_SYMPTOMS: Symptom[] = [
  { id: 'palpitations', name: 'Palpitations', icon: 'ecg_heart' },
  { id: 'chest_discomfort', name: 'Chest discomfort', icon: 'vital_signs' },
  { id: 'short_of_breath', name: 'Short of breath', icon: 'air' },
  { id: 'dizziness', name: 'Dizziness', icon: 'motion_photos_on' },
  { id: 'anxiety', name: 'Anxiety', icon: 'psychology' },
  { id: 'sweating', name: 'Sweating', icon: 'humidity_mid' },
  { id: 'nausea', name: 'Nausea', icon: 'sentiment_dissatisfied' },
  { id: 'other', name: 'Other', icon: 'add' },
];

export default function HighHeartRateAlert() {
  const [selectedSymptoms, setSelectedSymptoms] = useState<string[]>(['palpitations']);
  const [isLogged, setIsLogged] = useState(false);

  const toggleSymptom = (id: string) => {
    setSelectedSymptoms((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]
    );
  };

  const handleShare = () => {
    setIsLogged(true);
    setTimeout(() => {
      setIsLogged(false);
    }, 2500);
  };

  const handleFeelOkay = () => {
    setSelectedSymptoms([]);
  };

  return (
    <div className="min-h-screen bg-[#070708] text-white flex justify-center selection:bg-surface-container-high pb-safe">
      <div className="w-full max-w-md min-h-screen flex flex-col relative px-4 pb-12">
        {/* Top Navigation Header */}
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
        <main className="flex-1 w-full pt-4 flex flex-col">
          {/* Biometric Metric Ambient Strip / Compact Summary Card */}
          <div className="relative w-full rounded-2xl bg-[#1C1C1E] p-5 overflow-hidden mb-5 border border-white/5 shadow-sm">
            <div className="absolute -top-12 -right-12 w-36 h-36 rounded-full bg-[#EF4444]/10 blur-2xl pointer-events-none"></div>
            <div className="flex items-start justify-between relative z-10">
              <div className="flex items-center gap-3.5">
                <div className="w-11 h-11 rounded-full bg-zinc-900 border border-white/5 flex items-center justify-center text-[#EF4444] shrink-0">
                  <span className="material-symbols-outlined text-[22px]" style={{ fontVariationSettings: "'FILL' 1" }}>
                    favorite
                  </span>
                </div>
                <div className="flex flex-col">
                  <div className="flex items-baseline gap-1.5">
                    <span className="font-bold text-[40px] tabular-nums text-white leading-none tracking-tight">118</span>
                    <span className="font-semibold text-[13px] text-zinc-400 tracking-wider uppercase">BPM</span>
                  </div>
                  <span className="text-xs text-zinc-400 font-normal mt-1 leading-normal">Measured today at 2:45 PM</span>
                </div>
              </div>
              <div className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-[#EF4444]/15 border border-[#EF4444]/30 whitespace-nowrap">
                <span className="w-1.5 h-1.5 rounded-full bg-[#EF4444] animate-pulse"></span>
                <span className="font-bold text-[11px] text-[#EF4444] uppercase tracking-wider">• ABOVE RANGE</span>
              </div>
            </div>

            {/* Equalizer Waveform Bars */}
            <div className="mt-5 pt-3.5 flex items-center justify-between gap-2 w-full border-t border-white/5">
              <div className="h-4 flex-1 flex items-end gap-[3px]">
                {[2, 2.5, 2, 3, 4, 3.5, 2, 2.5, 3.5, 4, 3, 2, 1.5, 2, 3, 4, 2.5, 2, 2, 1.5, 2.5, 3, 4, 2.5, 1.5].map(
                  (h, i) => (
                    <span
                      key={i}
                      style={{ height: `${h * 4}px` }}
                      className="w-1 bg-[#EF4444]/60 rounded-full"
                    ></span>
                  )
                )}
              </div>
              <span className="text-xs font-medium text-zinc-400 whitespace-nowrap pl-2">Resting baseline: 64 BPM</span>
            </div>
          </div>

          {/* Editorial Context */}
          <div className="flex flex-col mb-4">
            <h2 className="font-bold text-2xl text-white tracking-tight">How are you feeling?</h2>
            <p className="font-normal text-sm leading-relaxed text-zinc-400 mt-1.5">
              Your heart rate is elevated while at rest. Recording how your body feels helps identify patterns and informs your care summary.
            </p>
          </div>

          {/* Interactive Symptom Multi-Select Grid */}
          <div className="grid grid-cols-2 gap-3 w-full mb-5">
            {HIGH_SYMPTOMS.map((symptom) => {
              const isSelected = selectedSymptoms.includes(symptom.id);
              return (
                <button
                  key={symptom.id}
                  onClick={() => toggleSymptom(symptom.id)}
                  type="button"
                  className={`text-left p-3.5 rounded-xl transition-all flex flex-col justify-between h-[96px] relative overflow-hidden group focus:outline-none border ${
                    isSelected
                      ? 'bg-[#2a2a2a] border-white/20 shadow-md scale-[1.01]'
                      : 'bg-[#1C1C1E] border-white/5 hover:bg-zinc-800'
                  }`}
                >
                  <div className="flex items-center justify-between w-full">
                    <div
                      className={`w-8 h-8 rounded-full flex items-center justify-center transition-colors ${
                        isSelected ? 'bg-[#1C1C1E] text-white' : 'bg-zinc-900 text-zinc-400'
                      }`}
                    >
                      <span className="material-symbols-outlined text-[18px]">{symptom.icon}</span>
                    </div>
                    <div
                      className={`w-5 h-5 rounded-full flex items-center justify-center transition-colors ${
                        isSelected ? 'bg-white text-zinc-950 font-bold' : 'bg-white/10 text-transparent'
                      }`}
                    >
                      <span className="material-symbols-outlined text-[13px] font-bold">check</span>
                    </div>
                  </div>
                  <span className="font-semibold text-sm text-zinc-200 tracking-tight">{symptom.name}</span>
                </button>
              );
            })}
          </div>

          {/* Clinical Advisory Card */}
          <div className="w-full rounded-xl bg-[#1C1C1E] border border-white/5 p-4 flex items-start gap-3.5 mb-4">
            <div className="w-8 h-8 rounded-full bg-[#EF4444]/10 border border-[#EF4444]/20 flex items-center justify-center text-[#EF4444] shrink-0 mt-0.5">
              <span className="material-symbols-outlined text-[18px]">medical_services</span>
            </div>
            <div className="flex flex-col min-w-0">
              <span className="font-bold text-[11px] text-[#EF4444] uppercase tracking-wider">Clinical Advisory</span>
              <p className="font-normal text-xs leading-relaxed text-zinc-400 mt-1">
                If you experience severe chest pain, fainting, difficulty breathing, or numbness, please rest immediately or contact local emergency services.
              </p>
            </div>
          </div>

          {/* Continuous Sensor Sync Card */}
          <div className="w-full rounded-xl bg-[#1C1C1E] border border-white/5 p-4 flex items-center justify-between mb-6">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-full bg-zinc-900 border border-white/5 flex items-center justify-center text-[#22C55E]">
                <span className="material-symbols-outlined text-[19px]">watch</span>
              </div>
              <div className="flex flex-col">
                <span className="font-semibold text-sm text-zinc-100 tracking-tight">Continuous Sensor Sync</span>
                <span className="font-normal text-xs text-zinc-400 mt-0.5">Signal strength strong · Optical 50Hz</span>
              </div>
            </div>
            <span className="font-bold text-[10px] text-[#22C55E] tracking-wider uppercase bg-[#22C55E]/10 px-2.5 py-1 rounded-full border border-[#22C55E]/20 whitespace-nowrap">
              Active
            </span>
          </div>

          {/* Bottom Actions */}
          <div className="flex flex-col gap-3 w-full mt-auto">
            <button
              onClick={handleShare}
              className="w-full h-12 rounded-full bg-zinc-100 text-zinc-950 font-semibold text-sm tracking-wide flex items-center justify-center gap-2 transition-all hover:bg-white active:scale-[0.98] shadow-md"
              type="button"
            >
              {isLogged ? (
                <>
                  <span className="material-symbols-outlined text-[18px] text-emerald-600">check_circle</span>
                  <span>Logged ({selectedSymptoms.length} Symptoms)</span>
                </>
              ) : (
                <>
                  <span>Share symptoms</span>
                  <span className="material-symbols-outlined text-[18px]">arrow_forward</span>
                </>
              )}
            </button>
            <button
              onClick={handleFeelOkay}
              className="w-full h-10 rounded-full bg-transparent text-zinc-400 font-semibold text-sm flex items-center justify-center transition-colors hover:text-zinc-200 hover:bg-zinc-800/40 active:scale-[0.98]"
              type="button"
            >
              I feel okay
            </button>
          </div>
        </main>
      </div>
    </div>
  );
}
