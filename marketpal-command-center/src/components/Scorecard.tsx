import React from 'react';
import { motion } from 'motion/react';
import { Shield, Zap, Eye, Users, Activity } from 'lucide-react';

interface ScoreFactor {
  name: string;
  score: number;
  weight: number;
  icon: React.ReactNode;
}

interface ScorecardProps {
  asset: string;
  totalScore: number;
  factors: ScoreFactor[];
}

export const Scorecard: React.FC<ScorecardProps> = ({ asset, totalScore, factors }) => {
  return (
    <div className="bg-[#151619] border border-[#141414] rounded-xl p-6 shadow-2xl">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h2 className="text-[#E4E3E0] font-mono text-xl font-bold tracking-tighter">{asset}</h2>
          <span className="text-[#8E9299] font-mono text-[10px] uppercase tracking-widest">Sniper Scorecard v2.1</span>
        </div>
        <div className="text-right">
          <div className={`text-4xl font-mono font-black ${totalScore > 70 ? 'text-[#00FF00]' : 'text-[#E4E3E0]'}`}>
            {totalScore}
          </div>
          <div className="text-[#8E9299] font-mono text-[10px] uppercase">Confidence Index</div>
        </div>
      </div>

      <div className="space-y-4">
        {factors.map((factor) => (
          <div key={factor.name} className="relative">
            <div className="flex justify-between items-center mb-1">
              <div className="flex items-center gap-2">
                <span className="text-[#8E9299]">{factor.icon}</span>
                <span className="text-[#E4E3E0] font-mono text-xs uppercase tracking-tight">{factor.name}</span>
              </div>
              <span className="text-[#E4E3E0] font-mono text-xs">{factor.score}/100</span>
            </div>
            <div className="h-1.5 w-full bg-[#141414] rounded-full overflow-hidden">
              <motion.div
                initial={{ width: 0 }}
                animate={{ width: `${factor.score}%` }}
                transition={{ duration: 1, ease: "easeOut" }}
                className={`h-full ${factor.score > 60 ? 'bg-[#00FF00]' : 'bg-[#8E9299]'}`}
              />
            </div>
            <div className="mt-1 text-right">
              <span className="text-[#8E9299] font-mono text-[8px] uppercase">Weight: {factor.weight * 100}%</span>
            </div>
          </div>
        ))}
      </div>

      <div className="mt-8 pt-6 border-t border-[#141414]">
        <button className="w-full py-3 bg-transparent border border-[#00FF00]/30 text-[#00FF00] font-mono text-xs uppercase tracking-widest hover:bg-[#00FF00] hover:text-[#151619] transition-all duration-200 rounded-lg">
          Execute Sniper Entry
        </button>
      </div>
    </div>
  );
};
