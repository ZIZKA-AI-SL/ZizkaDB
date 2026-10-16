export interface PricingPlan {
  name: string
  price: string
  sub: string
  features: readonly string[]
  cta: string
  href: string
  highlight: boolean
  ctaPrimary?: boolean
  note?: string
}

import { MANAGED_PLANS, startTrialHref } from "@/lib/plans";

const managedPricing = MANAGED_PLANS.map(
  (p): PricingPlan => ({
    name: p.name,
    price: p.price,
    sub: p.priceSub,
    features: p.features,
    cta: "Get started",
    href: startTrialHref(p.id),
    highlight: p.highlight,
    ctaPrimary: p.id === "pro",
  }),
);

export const LANDING_PRICING_PLANS: readonly PricingPlan[] = [
  {
    name: 'Self-Hosted',
    price: 'Free',
    sub: 'forever',
    features: ['1 agent, 1 API key', 'Your infrastructure', 'Docker Compose', 'Community support'],
    cta: 'Setup guide',
    href: '/docs',
    highlight: false,
    ctaPrimary: false,
  },
  ...managedPricing,
]
