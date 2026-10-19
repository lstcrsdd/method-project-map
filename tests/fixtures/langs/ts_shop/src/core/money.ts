/** Money helpers. */
export function sum(values: number[]): number {
  return values.reduce((a, b) => a + b, 0);
}

/** An amount of money. */
export class Money {
  constructor(public amount: number) {}
  add(other: Money): Money {
    return new Money(this.amount + other.amount);
  }
}
