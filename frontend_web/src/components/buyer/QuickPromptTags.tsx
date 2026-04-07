"use client";

import { motion } from "framer-motion";

const QUICK_PROMPTS = [
  "500 pcs MLCC capacitors 100nF 50V, CIF Mumbai",
  "1000 units STM32F4 IC chips, FOB Shenzhen",
  "2000 pcs USB-C connectors, DDP Berlin",
  "5000 pcs 10K resistors 0805, CIF São Paulo",
];

interface QuickPromptTagsProps {
  onSelect: (prompt: string) => void;
  disabled?: boolean;
}

export function QuickPromptTags({ onSelect, disabled }: QuickPromptTagsProps) {
  return (
    <div className="flex flex-wrap gap-2 mt-4">
      {QUICK_PROMPTS.map((prompt, i) => (
        <motion.button
          key={prompt}
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.5 + i * 0.1 }}
          onClick={() => onSelect(prompt)}
          disabled={disabled}
          className="px-3 py-1.5 text-xs text-gray-500 bg-gray-100 hover:bg-gray-200 rounded-full transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          aria-label={`Quick prompt: ${prompt}`}
        >
          {prompt}
        </motion.button>
      ))}
    </div>
  );
}
