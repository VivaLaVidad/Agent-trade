"use client";

import { motion } from "framer-motion";
import type { ProformaInvoice } from "@/lib/api/types";

interface ProformaInvoiceCardProps {
  invoice: ProformaInvoice;
  onConfirm: () => void;
  confirming?: boolean;
}

export function ProformaInvoiceCard({ invoice, onConfirm, confirming }: ProformaInvoiceCardProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 60, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: "spring", damping: 20, stiffness: 200 }}
      className="relative bg-white rounded-2xl shadow-xl border border-gray-100 overflow-hidden"
    >
      {/* Left accent bar */}
      <div className="absolute left-0 top-0 bottom-0 w-1.5 bg-gradient-to-b from-blue-500 to-blue-600" />

      <div className="p-6 pl-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-5">
          <div>
            <p className="text-xs font-mono text-gray-400 tracking-wider">PROFORMA INVOICE</p>
            <p className="text-lg font-semibold text-gray-900">{invoice.pi_number}</p>
          </div>
          <span className="px-3 py-1 text-xs font-medium bg-blue-50 text-blue-600 rounded-full">
            {invoice.incoterm}
          </span>
        </div>

        {/* Details grid */}
        <div className="grid grid-cols-2 gap-x-8 gap-y-3 text-sm mb-6">
          <div>
            <p className="text-gray-400 text-xs">Supplier</p>
            <p className="text-gray-800 font-medium">{invoice.supplier_name}</p>
          </div>
          <div>
            <p className="text-gray-400 text-xs">Buyer</p>
            <p className="text-gray-800 font-medium">{invoice.buyer_name}</p>
          </div>
          <div className="col-span-2">
            <p className="text-gray-400 text-xs">Product</p>
            <p className="text-gray-800">{invoice.product_description}</p>
          </div>
          <div>
            <p className="text-gray-400 text-xs">Quantity</p>
            <p className="text-gray-800 font-mono">{invoice.quantity.toLocaleString()} pcs</p>
          </div>
          <div>
            <p className="text-gray-400 text-xs">Unit Price</p>
            <p className="text-gray-800 font-mono">${invoice.unit_price_usd.toFixed(4)}</p>
          </div>
          <div>
            <p className="text-gray-400 text-xs">Total</p>
            <p className="text-xl font-bold text-gray-900">${invoice.total_usd.toFixed(2)}</p>
          </div>
          <div>
            <p className="text-gray-400 text-xs">Payment Terms</p>
            <p className="text-gray-800 text-xs">{invoice.payment_terms}</p>
          </div>
        </div>

        {/* Validity */}
        <p className="text-xs text-gray-400 mb-4">
          Valid for {invoice.validity_days} days from {new Date(invoice.created_at).toLocaleDateString()}
        </p>

        {/* CTA Button */}
        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          onClick={onConfirm}
          disabled={confirming}
          className="relative w-full py-3.5 rounded-xl bg-gradient-to-r from-blue-500 to-blue-600 text-white font-semibold text-sm shadow-lg shadow-blue-500/25 hover:shadow-blue-500/40 transition-shadow overflow-hidden disabled:opacity-60"
          aria-label="Confirm and pay"
        >
          {/* Shine sweep effect */}
          <motion.div
            className="absolute inset-0 bg-gradient-to-r from-transparent via-white/20 to-transparent"
            initial={{ x: "-100%" }}
            whileHover={{ x: "100%" }}
            transition={{ duration: 0.6 }}
          />
          <span className="relative z-10">
            {confirming ? "Processing..." : "Confirm & Pay"}
          </span>
        </motion.button>
      </div>
    </motion.div>
  );
}
