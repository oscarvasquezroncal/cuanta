import { type CartItem, cartTotal } from "../domain/cart";

export const cartStore = {
  items: [] as CartItem[],
  add(item: CartItem): void {
    this.items.push(item);
  },
  total(): number {
    return cartTotal(this.items);
  },
};
