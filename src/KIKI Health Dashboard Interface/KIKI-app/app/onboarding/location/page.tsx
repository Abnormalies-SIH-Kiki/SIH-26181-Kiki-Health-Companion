'use client';
import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import NamasteCalligraphy from '@/components/NamasteCalligraphy';

export default function Location() {
  const router = useRouter();
  const [cityOption, setCityOption] = useState('New Delhi');
  const [customCity, setCustomCity] = useState('');
  const [country] = useState('India');
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
    const finalCity = cityOption === 'OTHER' ? customCity.trim() : cityOption;
    if (cityOption === 'OTHER' && !finalCity) {
      alert('Please enter your city name to continue.');
      return;
    }
    localStorage.setItem('onboarding_city', finalCity || 'New Delhi');
    localStorage.setItem('onboarding_country', 'India');
    router.push('/onboarding/daily-goals');
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
            <span className="font-headline-md text-[20px] font-semibold text-on-surface text-center tracking-tight">Activity Baseline</span>
            <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center">
              <span className="material-symbols-outlined text-on-primary text-[18px]">person</span>
            </div>
          </div>
          <div className="w-full flex items-center gap-3 pt-2">
            <div className="flex-1 h-1 bg-surface-container-highest rounded-full overflow-hidden">
              <div className="h-full w-[66%] bg-tertiary-fixed rounded-full"></div>
            </div>
            <span className="font-label-caps text-[12px] font-bold text-on-surface-variant uppercase tracking-wider">STEP 2/3</span>
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
            <h1 className="text-[24px] sm:text-[26px] font-bold text-on-surface tracking-tight mt-1 leading-tight">Where are you based?</h1>
            <p className="text-[14px] font-normal text-on-surface-variant max-w-xs mx-auto leading-relaxed mt-0.5">
              This helps us calculate heat stress indexes, IMD weather warnings, and local environmental metrics.
            </p>
          </div>
        </div>

        <div className="flex flex-col gap-4 mt-3">
          <div className="flex flex-col gap-3">
            {/* City Selection Dropdown */}
            <div className="flex flex-col gap-1">
              <label className="font-label-caps text-[12px] font-bold text-on-surface-variant uppercase pl-1" htmlFor="city-select">
                City / Region
              </label>
              <div className="relative flex items-center">
                <span className="material-symbols-outlined absolute left-4 text-on-surface-variant text-[20px] pointer-events-none">location_city</span>
                <select 
                  className="w-full h-14 pl-12 pr-10 bg-surface-container rounded-lg font-body-lg text-[17px] text-on-surface appearance-none focus:outline-none focus:bg-surface-container-high transition-colors cursor-pointer" 
                  id="city-select"
                  value={cityOption}
                  onChange={(e) => setCityOption(e.target.value)}
                >
                  <option value="New Delhi">New Delhi</option>
                  <option value="Mumbai">Mumbai</option>
                  <option value="Kolkata">Kolkata</option>
                  <option value="Chennai">Chennai</option>
                  <option value="Pune">Pune</option>
                  <option value="Ahemdabad">Ahemdabad</option>
                  <option value="OTHER">Any other - please tell</option>
                </select>
                <span className="material-symbols-outlined absolute right-4 text-on-surface-variant text-[22px] pointer-events-none">expand_more</span>
              </div>
            </div>

            {/* Blank input when "Any other - please tell" is selected */}
            {cityOption === 'OTHER' && (
              <div className="flex flex-col gap-1 animate-in fade-in slide-in-from-top-2 duration-200">
                <label className="font-label-caps text-[12px] font-bold text-orange-400 uppercase pl-1" htmlFor="custom-city-input">
                  Enter Your City Name
                </label>
                <div className="relative flex items-center">
                  <span className="material-symbols-outlined absolute left-4 text-orange-400 text-[20px] pointer-events-none">edit_location</span>
                  <input 
                    className="w-full h-14 pl-12 pr-11 bg-surface-container rounded-lg font-body-lg text-[16px] text-on-surface placeholder-on-surface-variant/50 focus:outline-none focus:bg-surface-container-high border border-orange-500/40 transition-colors" 
                    id="custom-city-input" 
                    placeholder="e.g. Bengaluru, Jaipur, Lucknow..." 
                    type="text" 
                    value={customCity}
                    onChange={(e) => setCustomCity(e.target.value)}
                    autoFocus
                  />
                  {customCity && (
                    <button onClick={() => setCustomCity('')} aria-label="Clear city input" className="absolute right-3 w-8 h-8 rounded-full flex items-center justify-center text-on-surface-variant hover:text-on-surface transition-colors" type="button">
                      <span className="material-symbols-outlined text-[18px]">cancel</span>
                    </button>
                  )}
                </div>
              </div>
            )}

            {/* Country Locked to India */}
            <div className="flex flex-col gap-1">
              <label className="font-label-caps text-[12px] font-bold text-on-surface-variant uppercase pl-1" htmlFor="country-select">
                Country
              </label>
              <div className="relative flex items-center">
                <span className="material-symbols-outlined absolute left-4 text-on-surface-variant text-[20px] pointer-events-none">public</span>
                <select 
                  className="w-full h-14 pl-12 pr-10 bg-surface-container rounded-lg font-body-lg text-[17px] text-on-surface appearance-none focus:outline-none focus:bg-surface-container-high transition-colors cursor-default" 
                  id="country-select"
                  value="India"
                  disabled
                >
                  <option value="India">India (IN) 🇮🇳</option>
                </select>
                <span className="material-symbols-outlined absolute right-4 text-emerald-400 text-[20px] pointer-events-none">verified</span>
              </div>
            </div>
          </div>

          <div className="p-3.5 bg-surface-container flex items-start gap-3 mt-1 rounded-xl">
            <span className="material-symbols-outlined text-on-surface-variant text-[18px] mt-0.5">verified_user</span>
            <p className="text-[13px] font-normal text-on-surface-variant leading-relaxed tracking-normal">
              Your coordinates stay on-device. Location data is solely queried to pull ambient IMD weather telemetry and is never shared.
            </p>
          </div>

          <div className="mt-4 flex flex-col gap-2">
            <button onClick={handleNext} className="w-full h-14 rounded-xl bg-tertiary-fixed text-on-tertiary-fixed font-headline-md text-[20px] font-semibold flex items-center justify-center gap-2 shadow-lg hover:bg-tertiary active:scale-[0.98] transition-all duration-150" type="button">
              <span>Continue</span>
              <span className="material-symbols-outlined text-[22px]">arrow_forward</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
