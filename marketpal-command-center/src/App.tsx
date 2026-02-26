import React, { useState, useEffect } from 'react';
import { Scorecard } from './components/Scorecard';
import { SniperLog } from './components/SniperLog';
import { 
  Shield, Zap, Eye, Users, Activity, TrendingUp, Globe, Lock, Cpu, BarChart3, Crosshair
} from 'lucide-react';
import { 
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, AreaChart, Area
} from 'recharts';

const EQUITY_DATA = [
  { time: '00:00', value: 100000 }, { time: '04:00', value: 102500 },
  { time: '08:00', value: 101800 }, { time: '12:00', value: 105200 },
  { time: '16:00', value: 108900 }, { time: '20:00', value: 107500 },
  { time: '23:59', value: 112400 },
];

export default function App() {
  const [logs, setLogs] = useState<{ id: string; timestamp: string; type: any; message: string; severity: any }[]>([]);
  const [activeAsset, setActiveAsset] = useState('BTC-USD');
  
  // 🔥 STAV PRO ŽIVÁ DATA Z PYTHONU 🔥
  const [liveData, setLiveData] = useState({
    btcPrice: 0,
    status: "Navazuji spojení na 192.168.0.73...",
    osintScore: 50,
    portfolio: "Načítám bankéře..."
  });

  // Napojení na FastAPI Backend
  useEffect(() => {
    const fetchApiData = async () => {
      try {
        // TADY JE TA TVOJE SPRÁVNÁ IP ADRESA
        const response = await fetch('http://192.168.0.73:8000/api/status');
        const data = await response.json();
        
        setLiveData({
          btcPrice: data.market.btc_price,
          status: data.market.status,
          osintScore: data.market.osint_score,
          portfolio: data.portfolio
        });

        if (data.market.status.includes("NÁKUP") || data.market.status.includes("PRODEJ")) {
          setLogs(prev => {
            if (prev.length > 0 && prev[0].message === data.market.status) return prev;
            const newLog = {
              id: Math.random().toString(36).substr(2, 9),
              timestamp: new Date().toLocaleTimeString('en-GB', { hour12: false }),
              type: data.market.status.includes("NÁKUP") ? 'TA' : 'SYSTEM',
              message: data.market.status,
              severity: 'high'
            };
            return [newLog, ...prev.slice(0, 19)];
          });
        }
      } catch (error) {
        setLiveData(prev => ({ ...prev, status: "⚠️ ZTRÁTA SPOJENÍ S PYTHONEM" }));
      }
    };

    fetchApiData();
    const interval = setInterval(fetchApiData, 1000);
    return () => clearInterval(interval);
  }, []);

  const scorecardFactors = [
    { name: 'Technical Baseline', score: liveData.osintScore > 60 ? 82 : 40, weight: 0.2, icon: <Activity size={14} /> },
    { name: 'OSINT Infrastructure', score: liveData.osintScore, weight: 0.3, icon: <Globe size={14} /> },
    { name: 'Insider Sentiment', score: 50, weight: 0.3, icon: <Users size={14} /> },
    { name: 'Retail Counter-Flow', score: 75, weight: 0.2, icon: <Zap size={14} /> },
  ];

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-[#E4E3E0] font-sans selection:bg-[#00FF00] selection:text-[#0a0a0a]">
      <nav className="h-14 border-b border-[#141414] bg-[#151619] flex items-center justify-between px-6 sticky top-0 z-50">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <Crosshair className="text-[#00FF00]" size={20} />
            <span className="font-mono font-black text-lg tracking-tighter uppercase">MarketPal</span>
          </div>
        </div>
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full animate-pulse ${liveData.status.includes("ZTRÁTA") ? "bg-red-500" : "bg-[#00FF00]"}`} />
            <span className="font-mono text-[10px] uppercase tracking-widest text-[#8E9299]">Ghetto-Server: {liveData.status.includes("ZTRÁTA") ? "OFFLINE" : "ONLINE"}</span>
          </div>
        </div>
      </nav>

      <main className="p-6 grid grid-cols-12 gap-6 max-w-[1600px] mx-auto">
        <div className="col-span-12 lg:col-span-3 space-y-6">
          <div className="bg-[#151619] border border-[#141414] rounded-xl p-4">
            <h3 className="font-mono text-[10px] uppercase tracking-widest text-[#8E9299] mb-4">Tracked Assets</h3>
            <button className="w-full flex justify-between items-center p-3 rounded-lg border bg-[#00FF00]/10 border-[#00FF00]/30 text-[#00FF00]">
              <span className="font-mono font-bold">BTC-USD</span>
              <span className="font-mono text-sm">${liveData.btcPrice > 0 ? liveData.btcPrice.toLocaleString() : "..."}</span>
            </button>
          </div>

          <div className="bg-[#151619] border border-[#141414] rounded-xl p-4">
            <h3 className="font-mono text-[10px] uppercase tracking-widest text-[#8E9299] mb-4">Portfolio / Bankéř</h3>
            <div className="font-mono text-sm text-[#00FF00] border border-[#141414] p-3 rounded bg-[#0a0a0a]">
              {liveData.portfolio}
            </div>
          </div>
        </div>

        <div className="col-span-12 lg:col-span-6 space-y-6">
          <div className="bg-[#151619] border border-[#141414] rounded-xl p-6">
             <div className="flex items-center justify-between mb-6">
                <span className="px-2 py-1 text-[10px] font-mono text-yellow-500 border border-yellow-500/30 rounded bg-yellow-500/10">
                  {liveData.status}
                </span>
             </div>
            <div className="h-[300px] w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={EQUITY_DATA}>
                  <defs>
                    <linearGradient id="colorValue" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#00FF00" stopOpacity={0.3}/>
                      <stop offset="95%" stopColor="#00FF00" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#141414" vertical={false} />
                  <XAxis dataKey="time" stroke="#8E9299" fontSize={10} tickLine={false} axisLine={false} />
                  <YAxis stroke="#8E9299" fontSize={10} tickLine={false} axisLine={false} />
                  <Tooltip contentStyle={{ backgroundColor: '#151619', border: '#141414' }} itemStyle={{ color: '#00FF00' }} />
                  <Area type="monotone" dataKey="value" stroke="#00FF00" fillOpacity={1} fill="url(#colorValue)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>

        <div className="col-span-12 lg:col-span-3 space-y-6">
          <Scorecard asset={activeAsset} totalScore={liveData.osintScore} factors={scorecardFactors} />
          <div className="h-[400px]">
            <SniperLog entries={logs} />
          </div>
        </div>
      </main>
    </div>
  );
}