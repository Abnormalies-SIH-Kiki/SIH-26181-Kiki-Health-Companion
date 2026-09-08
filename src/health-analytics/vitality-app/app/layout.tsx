import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Vitality Health Dashboard',
  description: 'Premium Health & Fitness System',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <head>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet" />
        <link href="https://fonts.googleapis.com/css2?family=Hanken+Grotesk:wght@400;500;600;700;800&display=swap" rel="stylesheet" />
        <link href="https://fonts.googleapis.com/css2?family=Rozha+One&family=Yatra+One&display=swap" rel="stylesheet" />
        <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap" rel="stylesheet" />
      </head>
      <body className="bg-primary-container text-on-surface min-h-screen font-body-md overflow-x-hidden">
        <div className="noise-overlay"></div>
        {children}
      </body>
    </html>
  );
}
