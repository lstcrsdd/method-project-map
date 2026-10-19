package orders

import "example.com/shop/money"

// Order is what a customer buys.
type Order struct {
	Lines []money.Money
}

// Total adds the order lines.
func (o *Order) Total() money.Money {
	return money.Sum(o.Lines)
}
