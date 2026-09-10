import Link from 'next/link';

const alertTypes = [
  {
    id: 'high',
    title: 'High Heart Rate',
    desc: 'Log symptoms for elevated heart rate',
    icon: 'trending_up',
    color: 'text-red-400',
    bg: 'bg-red-900/20',
    border: 'border-red-500/20'
  },
  {
    id: 'low',
    title: 'Low Heart Rate',
    desc: 'Log symptoms for decreased heart rate',
    icon: 'trending_down',
    color: 'text-blue-400',
    bg: 'bg-blue-900/20',
    border: 'border-blue-500/20'
  }
];

export default function HeartRateAlertsMenu() {
  return (
    <div className="flex flex-col relative w-full pt-4 pb-28 bg-[#070708] min-h-screen px-4">
      <header className="flex items-center justify-between py-3 mb-2 pt-10">
        <Link href="/profile" className="w-11 h-11 flex items-center justify-center -ml-2 rounded-full text-zinc-400 hover:text-white transition-colors" type="button">
          <span className="material-symbols-outlined text-[24px]">arrow_back</span>
        </Link>
        <h1 className="text-xl font-bold tracking-tight text-white">Heart Rate Alerts</h1>
        <div className="w-11 h-11" />
      </header>

      <div className="mt-8 flex flex-col gap-4">
        {alertTypes.map((alert) => (
          <Link 
            key={alert.id}
            href={`/heart-rate-alerts/${alert.id}`} 
            className="flex items-center justify-between p-4 bg-[#141417] border border-zinc-800/80 rounded-2xl hover:bg-zinc-800/40 active:bg-zinc-800/70 transition-colors group"
          >
            <div className="flex items-center gap-4">
              <div className={`w-12 h-12 rounded-xl ${alert.bg} border ${alert.border} flex items-center justify-center ${alert.color} group-hover:scale-105 transition-transform`}>
                <span className="material-symbols-outlined text-[26px]">{alert.icon}</span>
              </div>
              <div>
                <h2 className="text-base font-semibold text-zinc-100">{alert.title}</h2>
                <p className="text-sm text-zinc-400 mt-0.5">{alert.desc}</p>
              </div>
            </div>
            <span className="material-symbols-outlined text-zinc-500 group-hover:text-zinc-300 transition-transform group-hover:translate-x-1">chevron_right</span>
          </Link>
        ))}
      </div>
    </div>
  );
}
