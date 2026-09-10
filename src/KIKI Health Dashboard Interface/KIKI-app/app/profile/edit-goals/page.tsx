'use client';

import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';

export default function EditGoals() {
  const router = useRouter();
  const [steps, setSteps] = useState(8000);
  const [calories, setCalories] = useState(500);
  const [loading, setLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    fetch('/api/user')
      .then((res) => res.json())
      .then((data) => {
        if (data.success && data.user) {
          const u = data.user;
          if (u.daily_steps_goal) setSteps(u.daily_steps_goal);
          if (u.daily_calories_goal) setCalories(u.daily_calories_goal);
        }
        setLoading(false);
      })
      .catch((err) => {
        console.error(err);
        setLoading(false);
      });
  }, []);

  const stepOptions = [
    { val: 5000, offset: 53.4 },
    { val: 8000, offset: 35.6 },
    { val: 10000, offset: 20.0 },
    { val: 12000, offset: 0.0 },
  ];

  const calOptions = [
    { val: 300, offset: 72.0 },
    { val: 500, offset: 48.0 },
    { val: 700, offset: 24.0 },
    { val: 900, offset: 0.0 },
  ];

  const currentStepOffset = stepOptions.find((o) => o.val === steps)?.offset ?? 35.6;
  const currentCalOffset = calOptions.find((o) => o.val === calories)?.offset ?? 48.0;

  const handleSave = async () => {
    setIsSaving(true);
    try {
      const res = await fetch('/api/user', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          type: 'goals',
          steps,
          calories,
        }),
      });

      if (res.ok) {
        router.push('/profile');
        router.refresh();
      } else {
        alert('Failed to update goals');
        setIsSaving(false);
      }
    } catch (e) {
      console.error(e);
      alert('An error occurred while updating goals.');
      setIsSaving(false);
    }
  };

  return (
    <div className="flex flex-col relative w-full pt-20 pb-12 bg-surface min-h-screen">
      {/* Top Header */}
      <header className="fixed top-0 w-full z-50 pt-safe bg-surface/90 backdrop-blur-xl border-b border-border-subtle shadow-[0_1px_8px_rgba(0,0,0,0.04)]">
        <div className="h-20 px-5 flex flex-col justify-center">
          <div className="flex items-center justify-between">
            <Link
              href="/profile"
              aria-label="Go back"
              className="w-11 h-11 flex items-center justify-center -ml-2 rounded-full text-on-surface-variant hover:text-on-surface transition-colors active:scale-95"
            >
              <span className="material-symbols-outlined text-[24px]">arrow_back</span>
            </Link>
            <span className="font-headline-md text-[20px] font-semibold text-on-surface text-center">
              Edit Goal Details
            </span>
            <div className="w-8 h-8 rounded-full bg-cyan-500/20 border border-cyan-500/40 flex items-center justify-center">
              <span className="material-symbols-outlined text-cyan-400 text-[18px]">track_changes</span>
            </div>
          </div>
          <div className="w-full flex items-center gap-3 pt-2">
            <div className="flex-1 h-1 bg-surface-container-highest rounded-full overflow-hidden">
              <div className="h-full w-full bg-cyan-400 rounded-full shadow-[0_0_8px_rgba(6,182,212,0.8)]"></div>
            </div>
            <span className="font-label-caps text-[11px] font-bold text-cyan-400 uppercase tracking-wider">
              EDIT MODE
            </span>
          </div>
        </div>
      </header>

      {/* Main Container */}
      <div className="flex flex-col w-full max-w-md mx-auto px-5 pb-8 mt-4">
        <div className="flex flex-col items-center justify-center pt-2 pb-5">
          <div className="text-center mt-3">
            <h1 className="font-headline-lg-mobile text-[26px] font-semibold text-on-surface">
              Customise Daily Targets
            </h1>
            <p className="font-body-md text-[15px] text-on-surface-variant mt-1">
              Adjust your goals. Rings on your dashboard will update automatically.
            </p>
          </div>
        </div>

        {loading ? (
          <div className="flex justify-center items-center py-16">
            <div className="w-8 h-8 rounded-full border-2 border-primary border-t-transparent animate-spin"></div>
          </div>
        ) : (
          <div className="flex flex-col gap-y-6">
            {/* Steps Goal Card */}
            <div className="flex flex-col bg-surface-container-low rounded-2xl p-5 border border-border-subtle shadow-sm">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2.5">
                  <div className="w-10 h-10 rounded-xl bg-step-blue/10 border border-step-blue/20 flex items-center justify-center text-step-blue">
                    <span className="material-symbols-outlined text-[20px]">directions_walk</span>
                  </div>
                  <div>
                    <h2 className="font-headline-sm text-[16px] font-bold text-on-surface">Daily Steps Goal</h2>
                    <span className="text-[12px] text-on-surface-variant font-medium">Recommended: 8,000+</span>
                  </div>
                </div>

                <div className="relative w-12 h-12 flex items-center justify-center">
                  <svg className="w-12 h-12 -rotate-90 transform" viewBox="0 0 36 36">
                    <path
                      className="text-surface-container-highest stroke-current"
                      d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
                      fill="none"
                      strokeWidth="3.5"
                    />
                    <path
                      className="text-step-blue stroke-current transition-all duration-500"
                      d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
                      fill="none"
                      strokeDasharray="100, 100"
                      strokeDashoffset={currentStepOffset}
                      strokeLinecap="round"
                      strokeWidth="3.5"
                    />
                  </svg>
                  <span className="absolute text-[10px] font-bold text-on-surface">
                    {Math.round((steps / 12000) * 100)}%
                  </span>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2.5">
                {stepOptions.map((opt) => (
                  <button
                    key={opt.val}
                    type="button"
                    onClick={() => setSteps(opt.val)}
                    className={`py-3 px-4 rounded-xl border text-sm font-semibold transition-all active:scale-95 flex items-center justify-between ${
                      steps === opt.val
                        ? 'bg-step-blue/15 border-step-blue text-step-blue shadow-sm'
                        : 'bg-surface-container border-white/[0.04] text-on-surface-variant hover:text-white'
                    }`}
                  >
                    <span>{opt.val.toLocaleString()}</span>
                    <span className="text-[11px] font-normal opacity-70">steps</span>
                  </button>
                ))}
              </div>
            </div>

            {/* Calories Goal Card */}
            <div className="flex flex-col bg-surface-container-low rounded-2xl p-5 border border-border-subtle shadow-sm">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2.5">
                  <div className="w-10 h-10 rounded-xl bg-calorie-green/10 border border-calorie-green/20 flex items-center justify-center text-calorie-green">
                    <span className="material-symbols-outlined text-[20px]">local_fire_department</span>
                  </div>
                  <div>
                    <h2 className="font-headline-sm text-[16px] font-bold text-on-surface">Active Calories Burn</h2>
                    <span className="text-[12px] text-on-surface-variant font-medium">Recommended: 500 kcal</span>
                  </div>
                </div>

                <div className="relative w-12 h-12 flex items-center justify-center">
                  <svg className="w-12 h-12 -rotate-90 transform" viewBox="0 0 36 36">
                    <path
                      className="text-surface-container-highest stroke-current"
                      d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
                      fill="none"
                      strokeWidth="3.5"
                    />
                    <path
                      className="text-calorie-green stroke-current transition-all duration-500"
                      d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
                      fill="none"
                      strokeDasharray="100, 100"
                      strokeDashoffset={currentCalOffset}
                      strokeLinecap="round"
                      strokeWidth="3.5"
                    />
                  </svg>
                  <span className="absolute text-[10px] font-bold text-on-surface">
                    {Math.round((calories / 900) * 100)}%
                  </span>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2.5">
                {calOptions.map((opt) => (
                  <button
                    key={opt.val}
                    type="button"
                    onClick={() => setCalories(opt.val)}
                    className={`py-3 px-4 rounded-xl border text-sm font-semibold transition-all active:scale-95 flex items-center justify-between ${
                      calories === opt.val
                        ? 'bg-calorie-green/15 border-calorie-green text-calorie-green shadow-sm'
                        : 'bg-surface-container border-white/[0.04] text-on-surface-variant hover:text-white'
                    }`}
                  >
                    <span>{opt.val}</span>
                    <span className="text-[11px] font-normal opacity-70">kcal</span>
                  </button>
                ))}
              </div>
            </div>

            {/* Action Buttons */}
            <div className="w-full pt-4 pb-4 flex flex-col gap-3">
              <button
                onClick={handleSave}
                disabled={isSaving}
                className="w-full py-4 px-6 rounded-xl bg-cyan-400 hover:bg-cyan-300 text-black font-headline-md text-[18px] font-bold flex items-center justify-center gap-2 active:scale-[0.98] transition-all shadow-[0_0_20px_rgba(6,182,212,0.4)] disabled:opacity-50"
                type="button"
              >
                {isSaving ? (
                  <>
                    <span className="w-5 h-5 rounded-full border-2 border-black border-t-transparent animate-spin"></span>
                    <span>Saving Goals...</span>
                  </>
                ) : (
                  <>
                    <span>Save Changes</span>
                    <span className="material-symbols-outlined text-[20px]">check</span>
                  </>
                )}
              </button>

              <Link
                href="/profile"
                className="w-full py-3 px-6 rounded-xl bg-surface-container text-on-surface-variant font-medium text-sm flex items-center justify-center transition-colors hover:text-white text-center"
              >
                Cancel
              </Link>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
