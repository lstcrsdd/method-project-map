using System;
using Shop.Orders;
using M = Shop.Money;

namespace Shop.Billing
{
    /// <summary>Creates invoices for orders.</summary>
    public class InvoiceService : BaseService, IIssuer
    {
        private readonly IRepo _repo;

        public InvoiceService(IRepo repo) { _repo = repo; }

        public M.Money Issue(Order order)
        {
            var money = M.Money.Sum(order.Count());
            Describe();
            _repo.Save(money);
            Console.WriteLine("issued");
            Helper.Log();
            mystery.Call();
            return money;
        }
    }
}
