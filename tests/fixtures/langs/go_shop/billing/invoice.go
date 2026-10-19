package billing

import (
	"fmt"

	m "example.com/shop/money"
	"example.com/shop/orders"
)

// Base gives every document a description.
type Base struct{}

// Describe says what the document is.
func (b *Base) Describe() string { return "base" }

// Invoice is a bill for an order.
type Invoice struct {
	Base
	Total m.Money
}

// Notifier tells someone about an invoice.
type Notifier interface {
	Notify(i *Invoice)
}

// NewInvoice bills an order.
func NewInvoice(o *orders.Order, n Notifier) *Invoice {
	inv := &Invoice{Total: o.Total()}
	inv.Describe()
	n.Notify(inv)
	fmt.Println(helper())
	_ = m.Sum(nil)
	mystery.Call()
	return inv
}
