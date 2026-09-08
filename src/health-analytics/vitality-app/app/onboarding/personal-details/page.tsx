'use client';
import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import NamasteCalligraphy from '@/components/NamasteCalligraphy';

export default function PersonalDetails() {
  const router = useRouter();
  const [name, setName] = useState('');
  const [gender, setGender] = useState('Prefer not to say');
  const [age, setAge] = useState(28);
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

  const handleNext = () => {
    localStorage.setItem('onboarding_name', name);
    localStorage.setItem('onboarding_gender', gender);
    localStorage.setItem('onboarding_age', age.toString());
    router.push('/onboarding/location');
  };

  const genders = ['Woman', 'Man', 'Non-binary', 'Prefer not to say'];

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
            <button aria-label="Go back" className="w-11 h-11 flex items-center justify-center -ml-2 rounded-full text-on-surface-variant hover:text-on-surface transition-colors" type="button">
              <span className="material-symbols-outlined text-[24px]">arrow_back</span>
            </button>
            <span className="font-headline-md text-[24px] font-semibold text-on-surface text-center">Personal Profile</span>
            <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center">
              <span className="material-symbols-outlined text-on-primary text-[18px]">person</span>
            </div>
          </div>
          <div className="w-full flex items-center gap-3 pt-2">
            <div className="flex-1 h-1 bg-surface-container-highest rounded-full overflow-hidden">
              <div className="h-full w-[33%] bg-tertiary-fixed rounded-full"></div>
            </div>
            <span className="font-label-caps text-[12px] font-bold text-on-surface-variant uppercase tracking-wider">STEP 1/3</span>
          </div>
        </div>
      </header>

      <div className="flex flex-col w-full px-5 pb-8 mt-2">
        <div className="flex flex-col items-center justify-center pt-1 pb-4">
          {/* Mascot Image with ambient glow */}
          <div className="relative flex items-center justify-center mb-1">
            <div className="absolute inset-0 bg-gradient-to-t from-orange-500/15 via-transparent to-green-500/10 rounded-full blur-2xl scale-95 pointer-events-none"></div>
            <div className="relative w-40 h-44 sm:w-44 sm:h-48 flex items-center justify-center">
              <img 
                src="/namaste_mascot.png" 
                alt="Namaste Fit India Mascot" 
                className="w-full h-full object-contain drop-shadow-[0_12px_24px_rgba(0,0,0,0.5)]"
              />
            </div>
          </div>

          {/* Calligraphic "NAMASTE" with plume flourish, sweeping loop, graduated dots & shiny flag colors */}
          <div className="w-full flex flex-col items-center text-center -mt-2">
            <NamasteCalligraphy />
            
            <h1 className="font-headline-lg-mobile text-[24px] sm:text-[26px] font-bold text-on-surface tracking-tight mt-1">Let’s get to know you</h1>
            <p className="font-body-md text-[14px] text-on-surface-variant mt-0.5">A few details help us personalise your daily targets.</p>
          </div>
        </div>

        <div className="flex flex-col gap-y-5">
          <div className="flex flex-col gap-2">
            <label className="font-label-caps text-[12px] font-bold text-on-surface-variant uppercase" htmlFor="full-name-input">Full Name</label>
            <div className="relative flex items-center bg-surface-container rounded-lg px-4 py-3.5 shadow-sm">
              <span className="material-symbols-outlined text-on-surface-variant text-[20px] mr-3">badge</span>
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

          <div className="flex flex-col gap-2">
            <label className="font-label-caps text-[12px] font-bold text-on-surface-variant uppercase">Gender</label>
            <div aria-label="Gender selection" className="grid grid-cols-2 gap-3" role="radiogroup">
              {genders.map(g => (
                <button 
                  key={g}
                  aria-checked={gender === g} 
                  className={`gender-pill flex items-center justify-between px-4 py-3.5 rounded-lg transition-all active:scale-[0.98] min-h-[52px] ${gender === g ? 'bg-surface-container-high text-tertiary font-semibold' : 'bg-surface-container text-on-surface'}`} 
                  onClick={() => setGender(g)} 
                  role="radio" 
                  type="button"
                >
                  <span className="font-body-md text-[16px] truncate">{g}</span>
                  <span className={`w-4 h-4 rounded-full flex items-center justify-center shrink-0 ${gender === g ? 'bg-tertiary' : 'bg-surface-container-highest'}`}>
                    {gender === g && <span className="w-1.5 h-1.5 rounded-full bg-on-tertiary"></span>}
                  </span>
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <label className="text-[11px] text-[#a09aab] uppercase tracking-wider font-semibold">AGE</label>
              <span className="text-[13px] text-[#c77dff] font-medium"><span className="text-white font-bold text-[15px]">{age}</span> years old</span>
            </div>
            <div className="relative flex items-center justify-center rounded-2xl p-4 border border-[#2d2540] shadow-sm overflow-hidden" style={{backgroundColor: '#1a1625'}}>
              <div className="flex items-center justify-between w-full max-w-[280px] z-10 select-none">
                <button type="button" onClick={() => setAge(Math.max(14, age - 1))} className="w-8 h-8 rounded-full flex items-center justify-center text-[#a09aab] hover:text-white transition-colors">
                  <span className="material-symbols-outlined text-[20px]">chevron_left</span>
                </button>
                <div className="flex items-center justify-center gap-4 flex-1">
                  <span className="text-[24px] text-white font-bold px-3 py-0.5 rounded-lg">{age}</span>
                </div>
                <button type="button" onClick={() => setAge(Math.min(100, age + 1))} className="w-8 h-8 rounded-full flex items-center justify-center text-[#a09aab] hover:text-white transition-colors">
                  <span className="material-symbols-outlined text-[20px]">chevron_right</span>
                </button>
              </div>
            </div>
          </div>

          <div className="w-full pt-8 pb-4">
            <button onClick={handleNext} className="w-full py-4 px-6 rounded-lg bg-primary-fixed text-on-primary-fixed font-headline-md text-[20px] font-semibold flex items-center justify-center gap-2 active:scale-[0.98] transition-transform shadow-md" type="button">
              <span>Continue</span>
              <span className="material-symbols-outlined text-[22px]">arrow_forward</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
