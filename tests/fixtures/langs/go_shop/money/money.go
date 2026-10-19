// Package money adds up cents.
package money

// Money is an amount in cents.
type Money struct {
	Cents int
}

// Sum adds amounts.
func Sum(xs []Money) Money {
	total := Money{}
	for _, x := range xs {
		total = total.Add(x)
	}
	return total
}

// Add returns the sum of two amounts.
func (m Money) Add(o Money) Money {
	return Money{Cents: m.Cents + o.Cents}
}
