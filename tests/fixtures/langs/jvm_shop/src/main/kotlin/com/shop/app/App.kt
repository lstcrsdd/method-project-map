package com.shop.app

import com.shop.billing.InvoiceService
import com.shop.orders.Order
import com.shop.orders.formatOrder

class FakeRepo : com.shop.billing.Repo {
    override fun save(money: com.shop.money.Money) {}
}

fun main() {
    val service = InvoiceService(FakeRepo())
    service.issue(Order("1"))
    println(formatOrder(Order("2")))
}
