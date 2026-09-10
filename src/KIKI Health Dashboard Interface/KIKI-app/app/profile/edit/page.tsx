'use client';

import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';

export default function EditProfile() {
  const router = useRouter();
  const [name, setName] = useState('');
  const [gender, setGender] = useState('Prefer not to say');
  const [age, setAge] = useState(28);
  const [loading, setLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    fetch('/api/user')
      .then((res) => res.json())
      .then((data) => {
        if (data.success && data.user) {
          const u = data.user;
          setName(`${u.first_name || ''} ${u.last_name || ''}`.trim());
          if (u.gender) setGender(u.gender);
          if (u.age) setAge(u.age);
        }
        setLoading(false);
      })
      .catch((err) => {
        console.error(err);
        setLoading(false);
      });
  }, []);

  const handleSave = async () => {
    setIsSaving(true);
    try {
      const res = await fetch('/api/user', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          type: 'profile',
          name,
          gender,
          age,
        }),
      });

      if (res.ok) {
        router.push('/profile');
        router.refresh();
      } else {
        alert('Failed to save profile changes');
        setIsSaving(false);
      }
    } catch (e) {
      console.error(e);
      alert('An error occurred while saving.');
      setIsSaving(false);
    }
  };

  const genders = ['Woman', 'Man', 'Non-binary', 'Prefer not to say'];

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
              Edit Profile
            </span>
            <div className="w-8 h-8 rounded-full bg-cyan-500/20 border border-cyan-500/40 flex items-center justify-center">
              <span className="material-symbols-outlined text-cyan-400 text-[18px]">person</span>
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
              Update Personal Details
            </h1>
            <p className="font-body-md text-[15px] text-on-surface-variant mt-1">
              Changes sync instantly across all dashboard and insights pages.
            </p>
          </div>
        </div>

        {loading ? (
          <div className="flex justify-center items-center py-16">
            <div className="w-8 h-8 rounded-full border-2 border-cyan-400 border-t-transparent animate-spin"></div>
          </div>
        ) : (
          <div className="flex flex-col gap-y-5">
            {/* Full Name Input */}
            <div className="flex flex-col gap-2">
              <label className="font-label-caps text-[12px] font-bold text-on-surface-variant uppercase" htmlFor="full-name-input">
                Full Name
              </label>
              <div className="relative flex items-center bg-surface-container rounded-xl px-4 py-3.5 shadow-sm border border-white/[0.05] focus-within:border-cyan-500/50 transition-colors">
                <span className="material-symbols-outlined text-cyan-400 text-[20px] mr-3">badge</span>
                <input
                  className="bg-transparent font-body-md text-[16px] text-on-surface w-full focus:outline-none placeholder-on-surface-variant/50"
                  id="full-name-input"
                  placeholder="e.g. Alex Lawson"
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
            </div>

            {/* Gender Radios */}
            <div className="flex flex-col gap-2">
              <label className="font-label-caps text-[12px] font-bold text-on-surface-variant uppercase">
                Gender
              </label>
              <div aria-label="Gender selection" className="grid grid-cols-2 gap-3" role="radiogroup">
                {genders.map((g) => (
                  <button
                    key={g}
                    aria-checked={gender === g}
                    className={`gender-pill flex items-center justify-between px-4 py-3.5 rounded-xl transition-all active:scale-[0.98] min-h-[52px] border ${
                      gender === g
                        ? 'bg-cyan-950/40 text-cyan-300 font-semibold border-cyan-500/50 shadow-[0_0_10px_rgba(6,182,212,0.15)]'
                        : 'bg-surface-container text-on-surface border-white/[0.04]'
                    }`}
                    onClick={() => setGender(g)}
                    role="radio"
                    type="button"
                  >
                    <span className="font-body-md text-[15px] truncate">{g}</span>
                    <span
                      className={`w-4 h-4 rounded-full flex items-center justify-center shrink-0 ${
                        gender === g ? 'bg-cyan-400' : 'bg-surface-container-highest'
                      }`}
                    >
                      {gender === g && <span className="w-1.5 h-1.5 rounded-full bg-black"></span>}
                    </span>
                  </button>
                ))}
              </div>
            </div>

            {/* Age Selector */}
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <label className="text-[11px] text-[#a09aab] uppercase tracking-wider font-semibold">AGE</label>
                <span className="text-[13px] text-cyan-300 font-medium">
                  <span className="text-white font-bold text-[15px]">{age}</span> years old
                </span>
              </div>
              <div
                className="relative flex items-center justify-center rounded-2xl p-4 border border-cyan-500/20 shadow-sm overflow-hidden bg-[#141417]"
              >
                <div className="flex items-center justify-between w-full max-w-[280px] z-10 select-none">
                  <button
                    type="button"
                    onClick={() => setAge(Math.max(14, age - 1))}
                    className="w-10 h-10 rounded-full bg-surface-container flex items-center justify-center text-neutral-300 hover:text-white transition-colors active:scale-95"
                  >
                    <span className="material-symbols-outlined text-[22px]">chevron_left</span>
                  </button>
                  <div className="flex items-center justify-center gap-4 flex-1">
                    <span className="text-[26px] text-white font-bold px-3 py-0.5 rounded-lg">{age}</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setAge(Math.min(100, age + 1))}
                    className="w-10 h-10 rounded-full bg-surface-container flex items-center justify-center text-neutral-300 hover:text-white transition-colors active:scale-95"
                  >
                    <span className="material-symbols-outlined text-[22px]">chevron_right</span>
                  </button>
                </div>
              </div>
            </div>

            {/* Action Buttons */}
            <div className="w-full pt-6 pb-4 flex flex-col gap-3">
              <button
                onClick={handleSave}
                disabled={isSaving}
                className="w-full py-4 px-6 rounded-xl bg-cyan-400 hover:bg-cyan-300 text-black font-headline-md text-[18px] font-bold flex items-center justify-center gap-2 active:scale-[0.98] transition-all shadow-[0_0_20px_rgba(6,182,212,0.4)] disabled:opacity-50"
                type="button"
              >
                {isSaving ? (
                  <>
                    <span className="w-5 h-5 rounded-full border-2 border-black border-t-transparent animate-spin"></span>
                    <span>Saving Changes...</span>
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
