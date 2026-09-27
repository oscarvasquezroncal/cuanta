"use client";

import { createCheckout } from "../lib/create-checkout";
import { cartStore } from "../stores/cart-store";

export function CartDrawer() {
  return (
    <aside aria-label="Shopping cart">
      <a href={createCheckout(cartStore.items)}>Checkout</a>
    </aside>
  );
}
