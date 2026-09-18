"use client";

import { useEffect, useMemo, useState } from "react";
import { Eye, Search, X, ShoppingCart, FileText } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// Matches the ₹-prefixed money formatting already used everywhere else in
// this admin panel (Payments/Payouts) for the common INR case, while still
// respecting the serializer's own `currency` field for any other value
// rather than assuming INR outright.
const fmtMoney = (amount: string, currency: string) =>
  `${currency === 'INR' ? '₹' : `${currency} `}${parseFloat(amount).toLocaleString()}`;

interface OrderItem {
  id: number;
  item_type: 'COURSE' | 'BUNDLE';
  course: number | null;
  course_title: string | null;
  bundle: number | null;
  bundle_name: string | null;
  title_snapshot: string;
  unit_price: string;
  quantity: number;
  total_price: string;
}

interface Order {
  id: number;
  order_number: string;
  student_name: string;
  student_email: string;
  status: 'PENDING' | 'PAID' | 'FAILED' | 'CANCELLED';
  subtotal: string;
  discount_amount: string;
  total_amount: string;
  currency: string;
  razorpay_order_id: string | null;
  items: OrderItem[];
  has_invoice: boolean;
  created_at: string;
  updated_at: string;
}

export default function OrdersLedger() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // This endpoint (GET /api/orders/orders/) is neither paginated (bare
  // array response, no {count,results}) nor filterable server-side (no
  // query params are read at all) -- staff/superuser simply gets every
  // order back in one response. Search is therefore done entirely
  // client-side against the array already in memory.
  const [search, setSearch] = useState("");

  const [selectedOrder, setSelectedOrder] = useState<Order | null>(null);

  const statusBadgeClass = (status: Order['status']) => {
    switch (status) {
      case 'PAID': return 'bg-green-500/10 text-green-400 border border-green-500/20';
      case 'PENDING': return 'bg-yellow-500/10 text-[#facc15] border border-yellow-500/20';
      case 'CANCELLED': return 'bg-zinc-500/10 text-zinc-400 border border-zinc-500/20';
      default: return 'bg-red-500/10 text-red-400 border border-red-500/20'; // FAILED
    }
  };

  const fetchOrders = async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/orders/orders/`, {
        credentials: "include"
      });

      if (res.ok) {
        const data: Order[] = await res.json();
        setOrders(Array.isArray(data) ? data : []);
      } else {
        setError("Failed to fetch orders");
      }
    } catch (err) {
      setError("Network error fetching orders");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchOrders();
  }, []);

  const filteredOrders = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return orders;
    return orders.filter(order =>
      order.order_number.toLowerCase().includes(term) ||
      order.student_name.toLowerCase().includes(term) ||
      order.student_email.toLowerCase().includes(term)
    );
  }, [orders, search]);

  const itemsSummary = (order: Order) => {
    if (order.items.length === 0) return <span className="text-zinc-600 italic">No items</span>;
    if (order.items.length === 1) return order.items[0].title_snapshot;
    return `${order.items[0].title_snapshot} +${order.items.length - 1} more`;
  };

  return (
    <div className="max-w-6xl mx-auto pb-20 font-sans text-white">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold">Orders</h1>
        <p className="text-zinc-400 text-sm mt-1">Every multi-item checkout order placed on the platform. Read-only -- orders are created and paid entirely by the student-facing checkout flow.</p>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-6 text-sm">{error}</div>}

      {/* Search Row -- client-side only, this endpoint has no server-side
          search/filter support at all. */}
      <div className="flex flex-col md:flex-row gap-4 mb-6">
        <div className="relative flex-1">
          <Search className="w-4 h-4 text-zinc-500 absolute left-4 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            placeholder="Search by order number, student name, or email..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full bg-zinc-900 border border-white/5 rounded-xl pl-11 pr-4 py-3 text-sm text-white focus:outline-none focus:border-[#facc15] transition-colors placeholder:text-zinc-500"
          />
        </div>
      </div>

      {/* Orders Table */}
      <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-white/5 border-b border-white/10 text-zinc-400 uppercase tracking-wider">
                <th className="p-4 font-semibold">Order #</th>
                <th className="p-4 font-semibold">Student</th>
                <th className="p-4 font-semibold">Items</th>
                <th className="p-4 font-semibold">Total Amount</th>
                <th className="p-4 font-semibold text-center">Status</th>
                <th className="p-4 font-semibold text-center">Invoice</th>
                <th className="p-4 font-semibold">Created</th>
                <th className="p-4 font-semibold text-center">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5 text-zinc-300">
              {loading ? (
                <tr>
                  <td colSpan={8} className="p-16 text-center text-zinc-500">
                    <div className="flex flex-col items-center justify-center gap-3">
                      <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
                      <span className="text-sm">Loading orders...</span>
                    </div>
                  </td>
                </tr>
              ) : filteredOrders.length === 0 ? (
                <tr>
                  <td colSpan={8} className="p-16 text-center text-zinc-500 text-sm">
                    {orders.length === 0 ? "No orders found." : "No orders match your search."}
                  </td>
                </tr>
              ) : (
                filteredOrders.map(order => (
                  <tr key={order.id} className="hover:bg-white/5 transition-colors cursor-pointer" onClick={() => setSelectedOrder(order)}>
                    <td className="p-4 font-bold text-white text-sm">{order.order_number}</td>

                    <td className="p-4">
                      <div className="font-semibold text-white">{order.student_name}</div>
                      <div className="text-zinc-500 text-[10px] mt-0.5">{order.student_email}</div>
                    </td>

                    <td className="p-4 max-w-xs truncate">{itemsSummary(order)}</td>

                    <td className="p-4 font-bold text-[#facc15] text-sm">
                      {fmtMoney(order.total_amount, order.currency)}
                    </td>

                    <td className="p-4 text-center">
                      <span className={`px-2.5 py-0.5 text-[9px] font-bold rounded-full ${statusBadgeClass(order.status)}`}>
                        {order.status}
                      </span>
                    </td>

                    <td className="p-4 text-center">
                      {order.has_invoice ? (
                        <span className="px-2.5 py-0.5 text-[9px] font-bold rounded-full bg-green-500/10 text-green-400 border border-green-500/20">
                          INVOICED
                        </span>
                      ) : (
                        <span className="px-2.5 py-0.5 text-[9px] font-bold rounded-full bg-zinc-500/10 text-zinc-500 border border-zinc-500/20">
                          NONE
                        </span>
                      )}
                    </td>

                    <td className="p-4 text-zinc-400">
                      {new Date(order.created_at).toLocaleString()}
                    </td>

                    <td className="p-4">
                      <div className="flex items-center justify-center gap-2">
                        <button
                          onClick={(e) => { e.stopPropagation(); setSelectedOrder(order); }}
                          className="p-2 bg-white/5 hover:bg-white/10 rounded-xl transition-all inline-flex items-center justify-center text-zinc-400 hover:text-white"
                          title="View Order Details"
                        >
                          <Eye className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* No pagination footer -- this endpoint returns a bare array with no
          server-side pagination at all. */}

      {/* Detail Modal */}
      {selectedOrder && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-white/10 p-6 rounded-2xl w-full max-w-lg shadow-2xl relative text-sm max-h-[85vh] overflow-y-auto">
            <button
              onClick={() => setSelectedOrder(null)}
              className="absolute top-4 right-4 p-2 bg-white/5 border border-white/5 hover:bg-white/10 rounded-full transition-colors text-zinc-400 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>

            <h2 className="text-xl font-bold mb-1 flex items-center gap-2">
              <ShoppingCart className="w-5 h-5 text-[#facc15]" />
              Order {selectedOrder.order_number}
            </h2>
            <p className="text-zinc-500 text-xs mb-6">Full item breakdown and gateway order tracking ID.</p>

            <div className="space-y-4">
              <div>
                <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Student</div>
                <div className="font-bold text-white">{selectedOrder.student_name}</div>
                <div className="text-xs text-zinc-400">{selectedOrder.student_email}</div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Total Amount</div>
                  <div className="font-bold text-[#facc15] text-base">
                    {fmtMoney(selectedOrder.total_amount, selectedOrder.currency)}
                  </div>
                </div>
                <div>
                  <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Status</div>
                  <div>
                    <span className={`px-2 py-0.5 text-[9px] font-bold rounded-full inline-block mt-0.5 ${statusBadgeClass(selectedOrder.status)}`}>
                      {selectedOrder.status}
                    </span>
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4 text-xs">
                <div>
                  <div className="text-zinc-500 mb-1">Subtotal</div>
                  <div className="text-zinc-300 font-semibold">{fmtMoney(selectedOrder.subtotal, selectedOrder.currency)}</div>
                </div>
                <div>
                  <div className="text-zinc-500 mb-1">Discount</div>
                  <div className="text-zinc-300 font-semibold">{fmtMoney(selectedOrder.discount_amount, selectedOrder.currency)}</div>
                </div>
              </div>

              {/* Item breakdown */}
              <div className="pt-4 border-t border-white/5">
                <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-2">Items ({selectedOrder.items.length})</div>
                <div className="space-y-2">
                  {selectedOrder.items.length === 0 ? (
                    <div className="text-zinc-600 italic text-xs">No items on this order.</div>
                  ) : (
                    selectedOrder.items.map(item => (
                      <div key={item.id} className="bg-black/40 p-3 rounded-lg border border-white/5">
                        <div className="flex items-start justify-between gap-2">
                          <div>
                            <div className="font-semibold text-white text-xs">{item.title_snapshot}</div>
                            <span className="mt-1 inline-block px-2 py-0.5 text-[9px] font-bold rounded-full bg-blue-500/10 text-blue-400 border border-blue-500/20">
                              {item.item_type}
                            </span>
                          </div>
                          <div className="text-right shrink-0">
                            <div className="text-zinc-400 text-[10px]">
                              {fmtMoney(item.unit_price, selectedOrder.currency)} &times; {item.quantity}
                            </div>
                            <div className="font-bold text-[#facc15] text-xs mt-0.5">
                              {fmtMoney(item.total_price, selectedOrder.currency)}
                            </div>
                          </div>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>

              <div className="pt-4 border-t border-white/5 space-y-3 font-mono text-xs">
                <div>
                  <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1 font-sans">Razorpay Order ID</div>
                  <div className="bg-black/40 p-2.5 rounded-lg border border-white/5 text-zinc-300 select-all">
                    {selectedOrder.razorpay_order_id || <span className="text-zinc-600 italic">None</span>}
                  </div>
                </div>
              </div>

              <div className="pt-4 border-t border-white/5 flex items-center justify-between text-xs">
                <span className="text-zinc-500 flex items-center gap-1.5">
                  <FileText className="w-3.5 h-3.5" />
                  Invoice
                </span>
                {selectedOrder.has_invoice ? (
                  <span className="px-2.5 py-0.5 text-[9px] font-bold rounded-full bg-green-500/10 text-green-400 border border-green-500/20">
                    INVOICE ISSUED
                  </span>
                ) : (
                  <span className="px-2.5 py-0.5 text-[9px] font-bold rounded-full bg-zinc-500/10 text-zinc-500 border border-zinc-500/20">
                    NO INVOICE
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
