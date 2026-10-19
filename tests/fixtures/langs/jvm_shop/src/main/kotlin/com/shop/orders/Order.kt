package com.shop.orders

/** A customer's order. */
data class Order(val id: String) {
    fun count(): Int = 1
}

fun formatOrder(order: Order): String = order.id
