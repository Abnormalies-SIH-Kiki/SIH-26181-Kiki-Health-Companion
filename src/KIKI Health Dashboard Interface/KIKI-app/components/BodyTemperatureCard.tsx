'use client';

import React, { useState } from 'react';

export default function BodyTemperatureCard() {
  const [unit, setUnit] = useState<'F' | 'C'>('F');
  
  const tempF = 98.3;
  const tempC = 36.8;

  const toggleUnit = (e: React.MouseEvent) => {
    e.preventDefault();
    setUnit(unit === 'F' ? 'C' : 'F');
  };

  return (
    <div className="block metric-group-glow rounded-xl p-6 relative overflow-hidden flex flex-col justify-between border border-border-subtle">
      <div className="flex justify-between items-center mb-3">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ backgroundColor: 'rgba(244, 63, 94, 0.12)' }}>
            <span className="material-symbols-outlined text-[20px]" style={{ color: '#f43f5e', fontVariationSettings: "'FILL' 1" }}>device_thermostat</span>
          </div>
          <h2 className="text-base font-semibold text-on-surface tracking-tight">Body Temperature</h2>
        </div>
        <div className="flex items-center gap-2">
          <span className="bg-emerald-500/15 text-emerald-500 border border-emerald-500/30 text-[12px] font-bold px-3 py-1 rounded-full whitespace-nowrap tracking-wider uppercase">
            NORMAL
          </span>
          <button 
            onClick={toggleUnit}
            className="flex items-center justify-center bg-surface-container-high hover:bg-surface-container-highest text-on-surface-variant rounded-md px-2.5 py-1 text-xs font-bold border border-border-subtle transition-colors ml-1"
            title={`Switch to °${unit === 'F' ? 'C' : 'F'}`}
          >
            °{unit === 'F' ? 'C' : 'F'}
          </button>
        </div>
      </div>
      
      <div className="flex items-baseline gap-2 mb-2 mt-1 relative z-10">
        <span className="text-[48px] font-bold text-on-surface leading-none tracking-tight">
          {unit === 'F' ? tempF : tempC}
        </span>
        <span className="text-[16px] font-semibold text-on-surface-variant tracking-wide">
          °{unit}
        </span>
      </div>
      
      <p className="text-[14px] font-normal text-on-surface-variant max-w-[80%] leading-relaxed relative z-10">
        Normal body temperature range is 97.0°F - 99.0°F (36.1°C - 37.2°C).
      </p>

      {/* Decorative background element matching heart rate/blood oxygen style */}
      <div className="absolute -bottom-8 -right-8 w-40 h-40 rounded-full bg-rose-500/5 blur-3xl pointer-events-none"></div>
    </div>
  );
}
