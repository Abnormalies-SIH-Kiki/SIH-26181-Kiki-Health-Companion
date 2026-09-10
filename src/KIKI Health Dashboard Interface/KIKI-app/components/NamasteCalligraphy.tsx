'use client';

import React from 'react';

export default function NamasteCalligraphy() {
  return (
    <div className="relative flex flex-col items-center justify-center select-none w-full max-w-[360px] mx-auto py-1">
      {/* Background ambient tricolor glow */}
      <div className="absolute inset-0 pointer-events-none flex items-center justify-center">
        <div className="w-64 h-20 bg-gradient-to-r from-orange-500/25 via-white/15 to-emerald-500/25 rounded-full blur-2xl transform -translate-y-2"></div>
      </div>

      <svg
        viewBox="0 0 380 150"
        className="w-full h-auto overflow-visible relative z-10 filter drop-shadow-[0_4px_16px_rgba(0,0,0,0.85)]"
        xmlns="http://www.w3.org/2000/svg"
      >
        <defs>
          {/* Indian Flag Tricolor Linear Gradient with metallic shiny stops */}
          <linearGradient id="tricolorGradient" x1="0%" y1="20%" x2="100%" y2="80%">
            <stop offset="0%" stopColor="#FF671F" />
            <stop offset="22%" stopColor="#FFA033" />
            <stop offset="38%" stopColor="#FFF3E0" />
            <stop offset="50%" stopColor="#FFFFFF" />
            <stop offset="62%" stopColor="#E8F5E9" />
            <stop offset="78%" stopColor="#10B981" />
            <stop offset="100%" stopColor="#046A38" />
          </linearGradient>

          {/* High gloss specular highlight for metallic sheen */}
          <linearGradient id="glossSheen" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stopColor="#FFFFFF" stopOpacity="0.75" />
            <stop offset="45%" stopColor="#FFFFFF" stopOpacity="0.2" />
            <stop offset="50%" stopColor="#000000" stopOpacity="0.1" />
            <stop offset="100%" stopColor="#FFFFFF" stopOpacity="0.35" />
          </linearGradient>

          {/* Plume 1 Gradient (Saffron) */}
          <linearGradient id="plumeSaffron" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#FFA500" />
            <stop offset="50%" stopColor="#FF671F" />
            <stop offset="100%" stopColor="#FF3D00" />
          </linearGradient>

          {/* Plume 2 Gradient (Golden White) */}
          <linearGradient id="plumeWhite" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#FFE0B2" />
            <stop offset="50%" stopColor="#FFFFFF" />
            <stop offset="100%" stopColor="#C8E6C9" />
          </linearGradient>

          {/* Plume 3 Gradient (Emerald Green) */}
          <linearGradient id="plumeGreen" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#86EFAC" />
            <stop offset="50%" stopColor="#138808" />
            <stop offset="100%" stopColor="#046A38" />
          </linearGradient>

          {/* Shimmer linear gradient for the sweep animation */}
          <linearGradient id="shimmerGrad" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="white" stopOpacity="0" />
            <stop offset="45%" stopColor="white" stopOpacity="0.1" />
            <stop offset="50%" stopColor="white" stopOpacity="0.9" />
            <stop offset="55%" stopColor="white" stopOpacity="0.1" />
            <stop offset="100%" stopColor="white" stopOpacity="0" />
          </linearGradient>

          {/* Filter for glowing outer aura */}
          <filter id="shinyAura" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="3" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
        </defs>

        <style>
          {`
            @keyframes shineSweep {
              0% { transform: translateX(-200px); }
              40% { transform: translateX(450px); }
              100% { transform: translateX(450px); }
            }
            .shine-bar {
              animation: shineSweep 4s ease-in-out infinite;
            }
            @keyframes glintTwinkle {
              0%, 100% { transform: scale(0.7) rotate(0deg); opacity: 0.3; }
              50% { transform: scale(1.2) rotate(45deg); opacity: 1; }
            }
            .glint {
              animation: glintTwinkle 3s ease-in-out infinite;
              transform-origin: center;
            }
          `}
        </style>

        {/* Top-Right Ornamental Plumes (Pattern from reference sketch) */}
        <g opacity="0.95">
          {/* Feather Plume 1 (Topmost Saffron wave) */}
          <path
            d="M 235 48 C 248 18, 282 8, 310 24 C 330 36, 348 55, 356 74 C 352 74, 335 56, 312 40 C 288 24, 252 28, 235 48 Z"
            fill="url(#plumeSaffron)"
            className="filter drop-shadow-[0_2px_6px_rgba(255,103,31,0.5)]"
          />

          {/* Feather Plume 2 (Middle upper Golden White wave) */}
          <path
            d="M 240 54 C 255 28, 288 22, 318 38 C 338 52, 354 74, 360 92 C 355 90, 340 70, 318 52 C 292 36, 260 40, 240 54 Z"
            fill="url(#plumeWhite)"
            className="filter drop-shadow-[0_2px_6px_rgba(255,255,255,0.6)]"
          />

          {/* Feather Plume 3 (Middle lower Emerald wave) */}
          <path
            d="M 245 60 C 262 38, 296 34, 324 50 C 342 64, 356 86, 360 104 C 356 102, 342 82, 322 64 C 298 48, 266 50, 245 60 Z"
            fill="url(#plumeGreen)"
            className="filter drop-shadow-[0_2px_6px_rgba(19,136,8,0.5)]"
          />

          {/* Feather Plume 4 (Bottom Green tail loop) */}
          <path
            d="M 250 66 C 270 48, 302 46, 328 62 C 344 76, 354 96, 356 112 C 352 110, 342 92, 324 76 C 300 62, 272 60, 250 66 Z"
            fill="url(#plumeGreen)"
            opacity="0.85"
          />
        </g>

        {/* Main "नमस्ते" Text in High-Contrast Devanagari Calligraphy */}
        <g id="namaste-text-group">
          {/* 3D Depth Shadow under lettering */}
          <text
            x="152"
            y="88"
            textAnchor="middle"
            fontFamily="'Rozha One', 'Yatra One', 'Noto Serif Devanagari', 'Poppins', serif"
            fontSize="54"
            fontWeight="900"
            letterSpacing="2"
            fill="#050507"
            opacity="0.9"
          >
            नमस्ते
          </text>

          {/* Primary Tricolor Calligraphic Fill */}
          <text
            x="150"
            y="86"
            textAnchor="middle"
            fontFamily="'Rozha One', 'Yatra One', 'Noto Serif Devanagari', 'Poppins', serif"
            fontSize="54"
            fontWeight="900"
            letterSpacing="2"
            fill="url(#tricolorGradient)"
            filter="url(#shinyAura)"
          >
            नमस्ते
          </text>

          {/* Glossy Metallic Sheen Layer */}
          <text
            x="150"
            y="86"
            textAnchor="middle"
            fontFamily="'Rozha One', 'Yatra One', 'Noto Serif Devanagari', 'Poppins', serif"
            fontSize="54"
            fontWeight="900"
            letterSpacing="2"
            fill="url(#glossSheen)"
            style={{ mixBlendMode: 'overlay' }}
          >
            नमस्ते
          </text>
        </g>

        {/* Shirorekha flourishes extending gracefully */}
        <g>
          {/* Top-left curl on the shirorekha */}
          <path
            d="M 72 52 C 62 48, 54 52, 52 59 C 50 67, 58 72, 66 69 C 74 66, 75 58, 70 54 Z"
            fill="#FF671F"
          />
          {/* Saffron accent stroke on top bar */}
          <path
            d="M 68 53 C 110 50, 180 50, 245 52 C 248 52, 248 54, 245 54 C 180 53, 110 53, 68 56 Z"
            fill="url(#tricolorGradient)"
          />
        </g>

        {/* Sweeping Underline Loop (Exact pattern from reference sketch) */}
        <g>
          {/* Main calligraphic underline with loop */}
          <path
            d="M 80 94 C 58 112, 54 130, 88 134 C 150 140, 230 136, 296 118 C 322 110, 338 98, 342 86 C 344 76, 336 68, 326 72 C 318 76, 318 86, 324 92 C 330 98, 340 98, 344 92 C 342 100, 326 112, 298 120 C 232 138, 150 142, 88 136 C 60 132, 64 116, 82 100 Z"
            fill="url(#tricolorGradient)"
            className="filter drop-shadow-[0_2px_8px_rgba(16,185,129,0.4)]"
          />

          {/* Inner flourish line for fine calligraphic detail */}
          <path
            d="M 88 98 C 66 115, 68 128, 92 131 C 154 136, 234 132, 296 115 C 320 108, 335 96, 338 88"
            fill="none"
            stroke="#FFFFFF"
            strokeWidth="1.2"
            strokeOpacity="0.75"
            strokeLinecap="round"
          />
        </g>

        {/* 3 Graduated Ornamental Dots on the right (Pattern from reference sketch) */}
        <g className="filter drop-shadow-[0_2px_6px_rgba(19,136,8,0.6)]">
          {/* Dot 1: Large */}
          <circle cx="352" cy="98" r="4.5" fill="#138808" />
          <circle cx="351" cy="97" r="1.5" fill="#FFFFFF" opacity="0.8" />

          {/* Dot 2: Medium */}
          <circle cx="359" cy="110" r="3.2" fill="#22C55E" />
          <circle cx="358.5" cy="109.5" r="1" fill="#FFFFFF" opacity="0.8" />

          {/* Dot 3: Small */}
          <circle cx="364" cy="120" r="2.2" fill="#86EFAC" />
        </g>

        {/* Shiny Sparkles / Glints */}
        {/* Saffron sparkle on left top */}
        <g transform="translate(68, 48)" className="glint">
          <path d="M 0 -7 Q 0 0 7 0 Q 0 0 0 7 Q 0 0 -7 0 Q 0 0 0 -7 Z" fill="#FFE082" />
          <circle cx="0" cy="0" r="1.5" fill="#FFFFFF" />
        </g>

        {/* Diamond White sparkle on central peak */}
        <g transform="translate(178, 48)" className="glint" style={{ animationDelay: '1.5s' }}>
          <path d="M 0 -9 Q 0 0 9 0 Q 0 0 0 9 Q 0 0 -9 0 Q 0 0 0 -9 Z" fill="#FFFFFF" />
          <circle cx="0" cy="0" r="2" fill="#FFFFFF" />
        </g>

        {/* Emerald sparkle on flourish */}
        <g transform="translate(325, 68)" className="glint" style={{ animationDelay: '0.8s' }}>
          <path d="M 0 -6 Q 0 0 6 0 Q 0 0 0 6 Q 0 0 -6 0 Q 0 0 0 -6 Z" fill="#86EFAC" />
        </g>
      </svg>
    </div>
  );
}
