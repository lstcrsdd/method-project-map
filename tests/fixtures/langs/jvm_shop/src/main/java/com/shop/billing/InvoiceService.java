package com.shop.billing;

import com.shop.money.Money;
import com.shop.orders.*;
import java.util.List;

/** Creates invoices for orders. */
public class InvoiceService extends BaseService implements Issuer {
    private final Repo repo;

    public InvoiceService(Repo repo) {
        this.repo = repo;
    }

    public Money issue(Order order) {
        Money money = Money.sum(order.count());
        describe();
        repo.save(money);
        money.add(money);
        Helper.log();
        unknown.call();
        return money;
    }
}
