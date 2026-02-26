import React from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { Terminal, AlertTriangle, Globe, Search } from 'lucide-react';

interface LogEntry {
  id: string;
  timestamp: string;
  type: 'OSINT' | 'TA' | 'INSIDER' | 'SYSTEM';
  message: string;
  severity: 'low' | 'medium' | 'high';
}

export const SniperLog: React.FC<{ entries: LogEntry[] }> = ({ entries }) => {
  return (
    <div className="bg-[#151619] border border-[#141414] rounded-xl flex flex-col h-full overflow-hidden">
      <div className="p-4 border-bottom border-[#141414] flex items-center justify-between bg-[#1a1b1e]">
        <div className="flex items-center gap-2">
          <Terminal size={14} className="text-[#00FF00]" />
          <span className="text-[#E4E3E0] font-mono text-xs uppercase tracking-widest">Live Intelligence Feed</span>
        </div>
        <div className="flex gap-1">
          <div className="w-2 h-2 rounded-full bg-[#00FF00] animate-pulse" />
          <div className="w-2 h-2 rounded-full bg-[#00FF00]/30" />
        </div>
      </div>
      
      <div className="flex-1 overflow-y-auto p-4 space-y-2 font-mono text-[11px]">
        <AnimatePresence initial={false}>
          {entries.map((entry) => (
            <motion.div
              key={entry.id}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              className="flex gap-3 border-b border-[#141414] pb-2 last:border-0"
            >
              <span className="text-[#8E9299] shrink-0">[{entry.timestamp}]</span>
              <span className={`shrink-0 font-bold ${
                entry.type === 'OSINT' ? 'text-blue-400' : 
                entry.type === 'INSIDER' ? 'text-purple-400' : 
                entry.type === 'TA' ? 'text-emerald-400' : 'text-gray-400'
              }`}>
                {entry.type}
              </span>
              <span className="text-[#E4E3E0]">{entry.message}</span>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
};
