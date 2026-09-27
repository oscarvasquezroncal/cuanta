export type CartItem = {
  id: string;
  quantity: number;
  price: number;
};

export function cartTotal(items: readonly CartItem[]): number {
  return items.reduce((total, item) => total + item.price * item.quantity, 0);
}
