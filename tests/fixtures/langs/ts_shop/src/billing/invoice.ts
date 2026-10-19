import { sum, Money, log } from "@core/index";
import type { Order } from "../models/order";
import { BaseService } from "../models/order";
import * as tax from "./tax";

export interface Repo {
  save(amount: Money): void;
}

/** Creates invoices for orders. */
export class InvoiceService extends BaseService {
  private total: Money = new Money(0);

  constructor(private repo: Repo) {
    super();
  }

  issue(order: Order): Money {
    const amount = new Money(sum(order.lines));
    this.describe();
    tax.apply(amount);
    log("issued");
    this.repo.save(amount);
    amount.add(this.total);
    unknownThing.run();
    return amount;
  }
}

export class SpecialInvoiceService extends InvoiceService {
  describe(): string {
    return "special";
  }
}
