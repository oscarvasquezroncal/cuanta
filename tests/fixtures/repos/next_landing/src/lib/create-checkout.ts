import { type CartItem, cartTotal } from "../domain/cart";

export function createCheckout(items: readonly CartItem[]): string {
  const total = cartTotal(items);
  return `/checkout?total=${total.toFixed(2)}`;
}
