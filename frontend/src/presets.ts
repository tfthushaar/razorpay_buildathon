// Hand-crafted example scenarios for the "break it" live demo panel. Each is a
// complete, valid TransactionScenario a presenter can submit as-is or tweak one number in before
// resubmitting — that tweak-and-resubmit motion is what makes the demo provably not scripted.

export const PRESETS: Record<string, object> = {
  "Duplicate refund": {
    orders: [
      { order_id: "demo_order_dup", merchant_id: "merchant_demo", amount: 500000, currency: "INR", created_at: "2026-01-01T10:00:00", rail: "upi" },
    ],
    payments: [
      {
        payment_id: "demo_pay_dup",
        order_id: "demo_order_dup",
        status: "captured",
        captured: true,
        captured_amount: 500000,
        fee_amount: 1500,
        tax_amount: 270,
        gateway: "HDFC",
        captured_at: "2026-01-01T10:05:00",
      },
    ],
    refunds: [
      { refund_id: "demo_refund_dup", payment_id: "demo_pay_dup", amount: 100000, status: "processed", created_at: "2026-01-02T10:00:00", refund_type: "partial" },
    ],
    settlements: [
      {
        settlement_id: "demo_stl_dup",
        payment_id: "demo_pay_dup",
        settled_amount: 500000 - 1500 - 270 - 2 * 100000,
        settlement_batch_id: "demo_batch_dup",
        utr: "111122223333",
        rail: "upi",
        settled_at: "2026-01-02T11:00:00",
        sla_days: 1,
      },
    ],
    ledger_entries: [
      { ledger_id: "demo_ldg_dup", order_id: "demo_order_dup", expected_amount: 500000 - 1500 - 270 - 100000, recorded_at: "2026-01-01T10:10:00" },
    ],
    provider: "mock",
  },

  "Netting trap (pair)": {
    orders: [
      { order_id: "demo_order_net_a", merchant_id: "merchant_demo", amount: 400000, currency: "INR", created_at: "2026-01-01T09:00:00", rail: "card" },
      { order_id: "demo_order_net_b", merchant_id: "merchant_demo", amount: 600000, currency: "INR", created_at: "2026-01-01T09:05:00", rail: "card" },
    ],
    payments: [
      { payment_id: "demo_pay_net_a", order_id: "demo_order_net_a", status: "captured", captured: true, captured_amount: 400000, fee_amount: 8000, tax_amount: 1440, gateway: "ICICI", captured_at: "2026-01-01T09:10:00" },
      { payment_id: "demo_pay_net_b", order_id: "demo_order_net_b", status: "captured", captured: true, captured_amount: 600000, fee_amount: 12000, tax_amount: 2160, gateway: "ICICI", captured_at: "2026-01-01T09:12:00" },
    ],
    refunds: [],
    settlements: [
      { settlement_id: "demo_stl_net_a", payment_id: "demo_pay_net_a", settled_amount: 400000 - 8000 - 1440 - 15000, settlement_batch_id: "demo_batch_net", utr: "222233334444", rail: "card", settled_at: "2026-01-03T09:00:00", sla_days: 2 },
      { settlement_id: "demo_stl_net_b", payment_id: "demo_pay_net_b", settled_amount: 600000 - 12000 - 2160 + 15000, settlement_batch_id: "demo_batch_net", utr: "333344445555", rail: "card", settled_at: "2026-01-03T09:00:00", sla_days: 2 },
    ],
    ledger_entries: [
      { ledger_id: "demo_ldg_net_a", order_id: "demo_order_net_a", expected_amount: 400000 - 8000 - 1440, recorded_at: "2026-01-01T09:15:00" },
      { ledger_id: "demo_ldg_net_b", order_id: "demo_order_net_b", expected_amount: 600000 - 12000 - 2160, recorded_at: "2026-01-01T09:15:00" },
    ],
    provider: "mock",
  },

  "Clean (control)": {
    orders: [
      { order_id: "demo_order_clean", merchant_id: "merchant_demo", amount: 250000, currency: "INR", created_at: "2026-01-01T10:00:00", rail: "upi" },
    ],
    payments: [
      { payment_id: "demo_pay_clean", order_id: "demo_order_clean", status: "captured", captured: true, captured_amount: 250000, fee_amount: 750, tax_amount: 135, gateway: "AXIS", captured_at: "2026-01-01T10:05:00" },
    ],
    refunds: [],
    settlements: [
      { settlement_id: "demo_stl_clean", payment_id: "demo_pay_clean", settled_amount: 250000 - 750 - 135, settlement_batch_id: "demo_batch_clean", utr: "444455556666", rail: "upi", settled_at: "2026-01-02T09:00:00", sla_days: 1 },
    ],
    ledger_entries: [
      { ledger_id: "demo_ldg_clean", order_id: "demo_order_clean", expected_amount: 250000 - 750 - 135, recorded_at: "2026-01-01T10:10:00" },
    ],
    provider: "mock",
  },
};
