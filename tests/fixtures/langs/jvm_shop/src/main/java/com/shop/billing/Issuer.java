package com.shop.billing;

import com.shop.money.Money;
import com.shop.orders.Order;

public interface Issuer {
    Money issue(Order order);
}
