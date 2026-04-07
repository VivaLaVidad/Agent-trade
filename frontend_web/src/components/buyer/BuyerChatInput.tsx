"use client";

import { useState, useRef } from "react";
import { motion } from "framer-motion";
import { Send } from "lucide-react";

interface BuyerChatInputProps {
  onSubmit: (text: string) => void;
  disabled?: boolean;
  value: string;
  onChange: (value: string) => void;
}

export function BuyerChatInput({ onSubmit, disabled, value, onChange }: BuyerChatInputProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [focused, setFocused] = useState(false);

  const handleSubmit = () => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSubmit(trimmed);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div
      className={`relative flex items-end gap-3 rounded-2xl border transition-all duration-300 p-4 ${
        focused
          ? "border-blue-300 shadow-lg shadow-blue-100"
          : "border-gray-200 shadow-md"
      }`}
    >
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        placeholder="Describe what you need — e.g. '500 pcs MLCC capacitors, CIF Mumbai'"
        rows={2}
        className="flex-1 resize-none bg-transparent text-gray-800 placeholder-gray-400 text-base leading-relaxed focus:outline-none disabled:opacity-50"
        aria-label="Trade inquiry input"
      />
      <motion.button
        whileHover={{ scale: 1.05 }}
        whileTap={{ scale: 0.95, rotate: -10 }}
        onClick={handleSubmit}
        disabled={disabled || !value.trim()}
        className="flex-shrink-0 p-3 rounded-xl bg-gradient-to-r from-blue-500 to-blue-600 text-white shadow-md hover:shadow-lg transition-shadow disabled:opacity-40 disabled:cursor-not-allowed"
        aria-label="Submit inquiry"
      >
        <Send className="w-5 h-5" />
      </motion.button>
    </div>
  );
}
