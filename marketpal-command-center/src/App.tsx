import React, { useState, useEffect } from 'react';
import { Scorecard } from './components/Scorecard';
import { SniperLog } from './components/SniperLog';
import { 
  Shield, 
  Zap, 
  Eye, 
  Users, 
  Activity, 
  TrendingUp, 
  Globe, 
  Lock, 
  Cpu,
  BarChart3,
  Crosshair
} from 'lucide-react';
import { 
  LineChart, 
  Line, 
  XAxis, 
  YAxis, 
  CartesianGrid, 
  Tooltip, 
  ResponsiveContainer,
  AreaChart,
  Area
} from 'recharts';

// Mock data for the dashboard
const EQUITY_DATA = [
  { time: '00:00', value: 100000 },
  { time: '04:00', value: 102500 },
  { time: '08:00', value: 101800 },
  { time: '12:00', value: 105200 },
  { time: '16:00', value: 108900 },
  { time: '20:00', value: 107500 },
  { time: '23:59', value: 112400 },
];

const INITIAL_LOGS = [
  { id: '1', timestamp: '03:00:12', type: 'OSINT', message: 'New domain registration detected: apple-vision-pro-v2.com', severity: 'medium' },
  { id: '2', timestamp: '03:00:15', type: 'INSIDER', message: 'Cluster buy detected at NVDA: CEO + CFO purchased $2.4M', severity: 'high' },
  { id: '3', timestamp: '03:01:02', type: 'TA', message: 'EURUSD RSI Oversold (24.2) on 1H timeframe', severity: 'low' },
  { id: '4', timestamp: '03:02:45', type: 'SYSTEM', message: 'VectorBT backtest completed for SOL-USD. Sharpe: 2.84', severity: 'low' },
];

export default function App() {
  const [logs, setLogs] = useState(INITIAL_LOGS);
  const [activeAsset, setActiveAsset] = useState('NVDA');

  // Simulate live log updates
  useEffect(() => {
    const interval = setInterval(() => {
      const newLog = {
        id: Math.random().toString(36).substr(2, 9),
        timestamp: new Date().toLocaleTimeString('en-GB', { hour12: false }),
        type: ['OSINT', 'TA', 'INSIDER', 'SYSTEM'][Math.floor(Math.random() * 4)] as any,
        message: 'Real-time anomaly detected in global macro flow...',
        severity: 'low' as const
      };
      setLogs(prev => [newLog, ...prev.slice(0, 19)]);
    }, 8000);
    return () => clearInterval(interval);
  }, []);

  const scorecardFactors = [
    { name: 'Technical Baseline', score: 82, weight: 0.2, icon: <Activity size={14} /> },
    { name: 'OSINT Infrastructure', score: 94, weight: 0.3, icon: <Globe size={14} /> },
    { name: 'Insider Sentiment', score: 88, weight: 0.3, icon: <Users size={14} /> },
    { name: 'Retail Counter-Flow', score: 75, weight: 0.2, icon: <Zap size={14} /> },
  ];

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-[#E4E3E0] font-sans selection:bg-[#00FF00] selection:text-[#0a0a0a]">
      {/* Top Navigation Rail */}
      <nav className="h-14 border-b border-[#141414] bg-[#151619] flex items-center justify-between px-6 sticky top-0 z-50">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <Crosshair className="text-[#00FF00]" size={20} />
            <span className="font-mono font-black text-lg tracking-tighter uppercase">MarketPal</span>
          </div>
          <div className="h-4 w-[1px] bg-[#141414]" />
          <div className="flex gap-4">
            {['Dashboard', 'Backtester', 'OSINT-Lab', 'Settings'].map(tab => (
              <button key={tab} className="text-[10px] uppercase tracking-widest font-mono text-[#8E9299] hover:text-[#00FF00] transition-colors">
                {tab}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-[#00FF00] animate-pulse" />
            <span className="font-mono text-[10px] uppercase tracking-widest text-[#8E9299]">Ghetto-Server: Online</span>
          </div>
          <div className="flex items-center gap-2 bg-[#1a1b1e] px-3 py-1 rounded border border-[#141414]">
            <Lock size={12} className="text-[#8E9299]" />
            <span className="font-mono text-[10px] text-[#8E9299]">AES-256 Encrypted</span>
          </div>
        </div>
      </nav>

      <main className="p-6 grid grid-cols-12 gap-6 max-w-[1600px] mx-auto">
        {/* Left Column: Asset Selection & Stats */}
        <div className="col-span-12 lg:col-span-3 space-y-6">
          <div className="bg-[#151619] border border-[#141414] rounded-xl p-4">
            <h3 className="font-mono text-[10px] uppercase tracking-widest text-[#8E9299] mb-4">Tracked Assets</h3>
            <div className="space-y-2">
              {['NVDA', 'AAPL', 'BTC-USD', 'EURUSD', 'GC=F'].map(asset => (
                <button
                  key={asset}
                  onClick={() => setActiveAsset(asset)}
                  className={`w-full flex justify-between items-center p-3 rounded-lg border transition-all ${
                    activeAsset === asset 
                    ? 'bg-[#00FF00]/10 border-[#00FF00]/30 text-[#00FF00]' 
                    : 'bg-[#1a1b1e] border-[#141414] text-[#8E9299] hover:border-[#8E9299]/30'
                  }`}
                >
                  <span className="font-mono font-bold">{asset}</span>
                  <TrendingUp size={14} className={activeAsset === asset ? 'opacity-100' : 'opacity-30'} />
                </button>
              ))}
            </div>
          </div>

          <div className="bg-[#151619] border border-[#141414] rounded-xl p-4">
            <h3 className="font-mono text-[10px] uppercase tracking-widest text-[#8E9299] mb-4">Portfolio Health</h3>
            <div className="space-y-4">
              <div>
                <div className="flex justify-between text-[10px] uppercase mb-1">
                  <span className="text-[#8E9299]">Drawdown</span>
                  <span className="text-red-400">2.4%</span>
                </div>
                <div className="h-1 bg-[#141414] rounded-full overflow-hidden">
                  <div className="h-full bg-red-400 w-[2.4%]" />
                </div>
              </div>
              <div>
                <div className="flex justify-between text-[10px] uppercase mb-1">
                  <span className="text-[#8E9299]">Daily Alpha</span>
                  <span className="text-[#00FF00]">+12.4%</span>
                </div>
                <div className="h-1 bg-[#141414] rounded-full overflow-hidden">
                  <div className="h-full bg-[#00FF00] w-[65%]" />
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Center Column: Scorecard & Charts */}
        <div className="col-span-12 lg:col-span-6 space-y-6">
          <div className="bg-[#151619] border border-[#141414] rounded-xl p-6">
            <div className="flex items-center justify-between mb-6">
              <div className="flex items-center gap-2">
                <BarChart3 size={18} className="text-[#00FF00]" />
                <h3 className="font-mono text-xs uppercase tracking-widest">Performance Matrix (VectorBT)</h3>
              </div>
              <div className="flex gap-2">
                {['1H', '4H', '1D', '1W'].map(tf => (
                  <button key={tf} className="px-2 py-1 text-[9px] font-mono border border-[#141414] rounded hover:bg-[#141414]">
                    {tf}
                  </button>
                ))}
              </div>
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
                  <XAxis 
                    dataKey="time" 
                    stroke="#8E9299" 
                    fontSize={10} 
                    tickLine={false} 
                    axisLine={false} 
                  />
                  <YAxis 
                    stroke="#8E9299" 
                    fontSize={10} 
                    tickLine={false} 
                    axisLine={false}
                    tickFormatter={(val) => `$${val/1000}k`}
                  />
                  <Tooltip 
                    contentStyle={{ backgroundColor: '#151619', border: '1px solid #141414', borderRadius: '8px' }}
                    itemStyle={{ color: '#00FF00', fontFamily: 'monospace' }}
                  />
                  <Area 
                    type="monotone" 
                    dataKey="value" 
                    stroke="#00FF00" 
                    fillOpacity={1} 
                    fill="url(#colorValue)" 
                    strokeWidth={2}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="bg-[#151619] border border-[#141414] rounded-xl p-4 flex items-center gap-4">
              <div className="p-3 bg-blue-500/10 rounded-lg">
                <Cpu className="text-blue-400" size={20} />
              </div>
              <div>
                <div className="text-[#8E9299] text-[10px] uppercase tracking-widest">Compute Load</div>
                <div className="text-xl font-mono font-bold">14.2%</div>
              </div>
            </div>
            <div className="bg-[#151619] border border-[#141414] rounded-xl p-4 flex items-center gap-4">
              <div className="p-3 bg-purple-500/10 rounded-lg">
                <Shield className="text-purple-400" size={20} />
              </div>
              <div>
                <div className="text-[#8E9299] text-[10px] uppercase tracking-widest">OSINT Nodes</div>
                <div className="text-xl font-mono font-bold">12 Active</div>
              </div>
            </div>
          </div>
        </div>

        {/* Right Column: Scorecard & Intelligence Feed */}
        <div className="col-span-12 lg:col-span-3 space-y-6">
          <Scorecard 
            asset={activeAsset} 
            totalScore={86} 
            factors={scorecardFactors} 
          />
          <div className="h-[400px]">
            <SniperLog entries={logs} />
          </div>
        </div>
      </main>

      {/* Status Bar */}
      <footer className="fixed bottom-0 left-0 right-0 h-8 bg-[#151619] border-t border-[#141414] flex items-center justify-between px-6 z-50">
        <div className="flex items-center gap-4">
          <span className="font-mono text-[9px] text-[#8E9299]">SYS_VER: 4.0.2-STABLE</span>
          <span className="font-mono text-[9px] text-[#8E9299]">LATENCY: 14ms</span>
        </div>
        <div className="flex items-center gap-4">
          <span className="font-mono text-[9px] text-[#00FF00]">MARKET_OPEN: NYSE</span>
          <span className="font-mono text-[9px] text-[#8E9299]">{new Date().toISOString()}</span>
        </div>
      </footer>
    </div>
  );
}
