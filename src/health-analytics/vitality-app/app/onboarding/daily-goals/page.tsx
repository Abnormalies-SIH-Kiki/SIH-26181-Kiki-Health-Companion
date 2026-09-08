'use client';
import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import NamasteCalligraphy from '@/components/NamasteCalligraphy';

export default function DailyGoals() {
  const router = useRouter();
  const [steps, setSteps] = useState(8000);
  const [calories, setCalories] = useState(500);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [setupStep, setSetupStep] = useState(0);
  const [isCheckingAuth, setIsCheckingAuth] = useState(true);

  useEffect(() => {
    fetch('/api/user')
      .then(res => res.json())
      .then(data => {
        if (data.success && data.user) {
          router.replace('/dashboard');
        } else {
          setIsCheckingAuth(false);
        }
      })
      .catch(() => {
        setIsCheckingAuth(false);
      });
  }, [router]);

  const stepOptions = [
    { val: 5000, offset: 53.4 },
    { val: 8000, offset: 35.6 },
    { val: 10000, offset: 20.0 },
    { val: 12000, offset: 0.0 }
  ];

  const calOptions = [
    { val: 300, offset: 72.0 },
    { val: 500, offset: 48.0 },
    { val: 700, offset: 24.0 },
    { val: 900, offset: 0.0 }
  ];

  const currentStepOffset = stepOptions.find(o => o.val === steps)?.offset || 35.6;
  const currentCalOffset = calOptions.find(o => o.val === calories)?.offset || 48.0;

  const handleSubmit = async () => {
    setIsSubmitting(true);
    setSetupStep(1);
    const name = localStorage.getItem('onboarding_name');
    const gender = localStorage.getItem('onboarding_gender');
    const age = localStorage.getItem('onboarding_age');
    const city = localStorage.getItem('onboarding_city');
    const country = localStorage.getItem('onboarding_country');

    try {
      const res = await fetch('/api/onboarding', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name, gender, age, city, country, steps, calories
        })
      });

      if (res.ok) {
        setSetupStep(2);
        // Clean onboarding storage keys
        localStorage.removeItem('onboarding_name');
        localStorage.removeItem('onboarding_gender');
        localStorage.removeItem('onboarding_age');
        localStorage.removeItem('onboarding_city');
        localStorage.removeItem('onboarding_country');

        // Intentional buffer time to show smooth setup transition
        await new Promise(resolve => setTimeout(resolve, 800));
        setSetupStep(3);
        await new Promise(resolve => setTimeout(resolve, 700));

        // Redirect to dashboard with full reload to rehydrate server data cleanly
        window.location.href = '/dashboard';
      } else {
        console.error('Failed to save');
        alert('Could not save your preferences. Please try again.');
        setIsSubmitting(false);
      }
    } catch (e) {
      console.error(e);
      alert('An error occurred during account creation.');
      setIsSubmitting(false);
    }
  };

  if (isCheckingAuth) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-surface">
        <div className="flex flex-col items-center gap-3">
          <div className="w-10 h-10 border-2 border-[#ff7034] border-t-transparent rounded-full animate-spin"></div>
          <p className="text-xs text-on-surface-variant font-medium">Checking session...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col relative w-full pt-20 pb-12 bg-surface min-h-screen">
      <header className="fixed top-0 w-full z-50 pt-safe bg-surface/80 backdrop-blur-xl shadow-[0_1px_8px_rgba(0,0,0,0.04)]">
        <div className="h-20 px-5 flex flex-col justify-center">
          <div className="flex items-center justify-between">
            <button onClick={() => router.back()} aria-label="Go back" className="w-11 h-11 flex items-center justify-center -ml-2 rounded-full text-on-surface-variant hover:text-on-surface transition-colors" type="button">
              <span className="material-symbols-outlined text-[24px]">arrow_back</span>
            </button>
            <span className="font-headline-md text-[20px] font-semibold text-on-surface text-center">Health Goals</span>
            <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center">
              <span className="material-symbols-outlined text-on-primary text-[18px]">person</span>
            </div>
          </div>
          <div className="w-full flex items-center gap-3 pt-2">
            <div className="flex-1 h-1 bg-surface-container-highest rounded-full overflow-hidden">
              <div className="h-full w-full bg-tertiary-fixed rounded-full" style={{backgroundColor: '#ff7034'}}></div>
            </div>
            <span className="font-label-caps text-[12px] font-bold text-on-surface-variant uppercase tracking-wider">STEP 3/3</span>
          </div>
        </div>
      </header>

      <div className="flex flex-col w-full px-5 pb-8 mt-2">
        {/* Mascot Image + Calligraphic Namaste Greeting */}
        <div className="flex flex-col items-center justify-center pt-1 pb-3">
          <div className="relative flex items-center justify-center mb-1">
            <div className="absolute inset-0 bg-gradient-to-t from-orange-500/15 via-transparent to-green-500/10 rounded-full blur-2xl scale-95 pointer-events-none"></div>
            <div className="relative w-36 h-40 sm:w-40 sm:h-44 flex items-center justify-center">
              <img 
                src="/namaste_mascot.png" 
                alt="Namaste Fit India Mascot" 
                className="w-full h-full object-contain drop-shadow-[0_12px_24px_rgba(0,0,0,0.5)]"
              />
            </div>
          </div>

          <div className="w-full flex flex-col items-center text-center -mt-2">
            <NamasteCalligraphy />
            <h1 className="text-[24px] sm:text-[26px] font-bold text-on-surface tracking-tight mt-1 leading-tight">Set your daily goals</h1>
            <p className="text-[14px] font-normal text-on-surface-variant max-w-xs mx-auto leading-relaxed mt-0.5">
              Personalise your baseline steps and active burn target. You can update these anytime.
            </p>
          </div>
        </div>

        <div className="flex flex-col gap-4 w-full">
          {/* Daily Steps Goal Card */}
          <div className="w-full bg-surface-container rounded-lg p-5 flex flex-col shadow-md relative overflow-hidden">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-full flex items-center justify-center" style={{backgroundColor: 'rgba(255, 112, 52, 0.15)'}}>
                  <span className="material-symbols-outlined text-[22px]" style={{color: '#ff7034'}}>directions_walk</span>
                </div>
                <div className="flex flex-col">
                  <span className="text-[20px] text-on-surface font-semibold">Daily Steps</span>
                  <span className="text-[12px] font-bold text-on-surface-variant uppercase tracking-wider">Baseline target</span>
                </div>
              </div>
              <div className="relative w-11 h-11 flex items-center justify-center">
                <svg className="w-full h-full -rotate-90" viewBox="0 0 44 44">
                  <circle className="text-surface-container-highest" cx="22" cy="22" fill="none" r="17" stroke="currentColor" strokeWidth="4"></circle>
                  <circle className="transition-all duration-300" cx="22" cy="22" fill="none" r="17" stroke="currentColor" strokeDasharray="106.8" strokeDashoffset={currentStepOffset} strokeLinecap="round" strokeWidth="4" style={{stroke: '#ff7034'}}></circle>
                </svg>
                <span className="material-symbols-outlined absolute text-[14px]" style={{color: '#ff7034'}}>done</span>
              </div>
            </div>
            <div className="flex items-baseline gap-2 mb-5">
              <span className="font-display-stat text-[48px] font-bold text-on-surface tracking-tight leading-none">{steps.toLocaleString()}</span>
              <span className="text-[16px] text-on-surface-variant font-medium">steps / day</span>
            </div>
            <div className="grid grid-cols-4 gap-2">
              {stepOptions.map(opt => (
                <button 
                  key={opt.val} 
                  onClick={() => setSteps(opt.val)}
                  className={`py-2.5 px-1 rounded-full text-center transition-all duration-200 active:scale-95 text-xs font-semibold ${steps === opt.val ? 'shadow-md font-bold' : ''}`}
                  style={steps === opt.val 
                    ? {backgroundColor: '#ff7034', color: '#131313', border: '1px solid #ff7034', boxShadow: 'rgba(255, 112, 52, 0.4) 0px 4px 14px'} 
                    : {backgroundColor: 'rgba(255, 255, 255, 0.05)', color: '#c4c4c4', border: '1px solid rgba(255, 255, 255, 0.08)'}
                  }
                  type="button"
                >
                  {opt.val.toLocaleString()}
                </button>
              ))}
            </div>
          </div>

          {/* Calories Goal Card */}
          <div className="w-full bg-surface-container rounded-lg p-5 flex flex-col shadow-md relative overflow-hidden">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-full bg-secondary-container flex items-center justify-center">
                  <span className="material-symbols-outlined text-secondary-fixed text-[22px]">mode_heat</span>
                </div>
                <div className="flex flex-col">
                  <span className="text-[20px] text-on-surface font-semibold">Calories to Burn</span>
                  <span className="text-[12px] font-bold text-on-surface-variant uppercase tracking-wider">Active burn target</span>
                </div>
              </div>
              <div className="relative w-11 h-11 flex items-center justify-center">
                <svg className="w-full h-full -rotate-90" viewBox="0 0 44 44">
                  <circle className="text-surface-container-highest" cx="22" cy="22" fill="none" r="17" stroke="currentColor" strokeWidth="4"></circle>
                  <circle className="text-secondary-fixed transition-all duration-300" cx="22" cy="22" fill="none" r="17" stroke="currentColor" strokeDasharray="106.8" strokeDashoffset={currentCalOffset} strokeLinecap="round" strokeWidth="4"></circle>
                </svg>
                <span className="material-symbols-outlined absolute text-[14px] text-secondary">bolt</span>
              </div>
            </div>
            <div className="flex items-baseline gap-2 mb-5">
              <span className="font-display-stat text-[48px] font-bold text-on-surface tracking-tight leading-none">{calories}</span>
              <span className="text-[16px] text-on-surface-variant font-medium">kcal / day</span>
            </div>
            <div className="grid grid-cols-4 gap-2">
              {calOptions.map(opt => (
                <button 
                  key={opt.val} 
                  onClick={() => setCalories(opt.val)}
                  className={`py-2.5 px-1 rounded-full text-center transition-all duration-200 active:scale-95 text-xs font-semibold ${calories === opt.val ? 'shadow-md font-bold' : ''}`}
                  style={calories === opt.val 
                    ? {backgroundColor: '#ff7034', color: '#131313', border: '1px solid #ff7034', boxShadow: 'rgba(255, 112, 52, 0.4) 0px 4px 14px'} 
                    : {backgroundColor: 'rgba(255, 255, 255, 0.05)', color: '#c4c4c4', border: '1px solid rgba(255, 255, 255, 0.08)'}
                  }
                  type="button"
                >
                  {opt.val}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="w-full flex flex-col items-center mt-8 gap-3">
          <button 
            onClick={handleSubmit}
            disabled={isSubmitting}
            className="w-full py-4 rounded-xl bg-on-surface text-on-primary text-[18px] font-semibold flex items-center justify-center gap-2 shadow-lg active:scale-[0.98] transition-transform" 
            style={{backgroundColor: '#ff7034', color: '#131313', boxShadow: 'rgba(255, 112, 52, 0.35) 0px 4px 20px'}} 
            type="button"
          >
            {isSubmitting ? (
              <span className="material-symbols-outlined animate-spin text-[20px]">progress_activity</span>
            ) : (
              <>
                <span>Save goals & continue</span>
                <span className="material-symbols-outlined text-[20px]">arrow_forward</span>
              </>
            )}
          </button>
        </div>
      </div>

      {/* Setup Buffer Overlay */}
      {isSubmitting && (
        <div className="fixed inset-0 z-50 bg-black/90 backdrop-blur-xl flex flex-col items-center justify-center p-6 animate-in fade-in duration-300">
          <div className="max-w-sm w-full flex flex-col items-center text-center space-y-6">
            <div className="relative flex items-center justify-center w-24 h-24">
              <div className="absolute inset-0 rounded-full bg-orange-500/20 blur-xl animate-pulse"></div>
              <div className="relative w-20 h-20 rounded-2xl bg-neutral-900 border border-orange-500/40 flex items-center justify-center shadow-xl shadow-orange-500/20">
                <span className="material-symbols-outlined text-orange-400 text-[36px] animate-spin">
                  sync
                </span>
              </div>
            </div>

            <div className="space-y-2">
              <h3 className="text-xl font-bold text-white tracking-tight">Setting Up Your Account</h3>
              <p className="text-sm text-neutral-400 min-h-[20px] transition-all">
                {setupStep <= 1 && 'Saving your personal health profile...'}
                {setupStep === 2 && 'Calibrating daily activity baselines...'}
                {setupStep >= 3 && 'Finalizing & preparing your dashboard...'}
              </p>
            </div>

            <div className="w-full bg-neutral-800/80 rounded-full h-2 overflow-hidden border border-white/5">
              <div
                className="h-full bg-gradient-to-r from-orange-500 to-amber-400 rounded-full transition-all duration-500 ease-out"
                style={{
                  width: setupStep <= 1 ? '35%' : setupStep === 2 ? '70%' : '100%'
                }}
              ></div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
