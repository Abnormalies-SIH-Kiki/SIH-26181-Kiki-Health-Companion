'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';

export default function DeleteAccountButton() {
  const router = useRouter();
  const [isOpen, setIsOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  const handleDelete = async () => {
    setIsDeleting(true);
    try {
      const res = await fetch('/api/user', { method: 'DELETE' });
      if (res.ok) {
        // Clear all local onboarding & cached keys
        localStorage.removeItem('onboarding_name');
        localStorage.removeItem('onboarding_gender');
        localStorage.removeItem('onboarding_age');
        localStorage.removeItem('onboarding_city');
        localStorage.removeItem('onboarding_country');
        
        // Full reload redirect to restart fresh onboarding
        window.location.href = '/onboarding/personal-details';
      } else {
        alert('Failed to delete account. Please try again.');
        setIsDeleting(false);
        setIsOpen(false);
      }
    } catch (err) {
      console.error('Delete account error:', err);
      alert('An error occurred while deleting account.');
      setIsDeleting(false);
      setIsOpen(false);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setIsOpen(true)}
        className="w-full flex items-center justify-between p-3.5 hover:bg-red-500/10 active:bg-red-500/20 transition-colors group text-left"
      >
        <div className="flex items-center gap-3.5">
          <div className="w-8 h-8 rounded-lg bg-red-950/40 border border-red-500/30 flex items-center justify-center text-red-400 group-hover:border-red-400/60 transition-colors">
            <span className="material-symbols-outlined text-[19px]">delete</span>
          </div>
          <div>
            <span className="text-sm font-medium text-red-400 group-hover:text-red-300 transition-colors">Delete My Account</span>
            <p className="text-[11px] text-zinc-400">Permanently reset and start fresh</p>
          </div>
        </div>
        <span className="material-symbols-outlined text-zinc-500 group-hover:text-red-400 text-[18px]">chevron_right</span>
      </button>

      {/* Confirmation Modal */}
      {isOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-200">
          <div className="bg-[#141417] border border-red-500/30 rounded-2xl p-6 max-w-sm w-full shadow-2xl shadow-red-950/50 space-y-4">
            <div className="w-12 h-12 rounded-full bg-red-500/15 border border-red-500/30 flex items-center justify-center text-red-400 mx-auto">
              <span className="material-symbols-outlined text-[28px]">warning</span>
            </div>

            <div className="text-center space-y-1.5">
              <h3 className="text-lg font-bold text-white">Delete Account?</h3>
              <p className="text-xs text-zinc-400 leading-relaxed">
                This will permanently delete your profile, health metrics, and personal targets. You will be taken back to the initial onboarding screen to start fresh.
              </p>
            </div>

            <div className="flex gap-3 pt-2">
              <button
                type="button"
                disabled={isDeleting}
                onClick={() => setIsOpen(false)}
                className="flex-1 py-2.5 px-4 rounded-xl bg-zinc-800/80 hover:bg-zinc-700 text-zinc-300 text-xs font-semibold transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={isDeleting}
                onClick={handleDelete}
                className="flex-1 py-2.5 px-4 rounded-xl bg-red-600 hover:bg-red-500 active:bg-red-700 text-white text-xs font-semibold flex items-center justify-center gap-1.5 transition-colors shadow-lg shadow-red-600/30 disabled:opacity-50"
              >
                {isDeleting ? (
                  <>
                    <span className="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
                    <span>Deleting...</span>
                  </>
                ) : (
                  <span>Yes, Delete</span>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
