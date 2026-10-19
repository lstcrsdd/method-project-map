using Shop.Billing;

var service = new InvoiceService(new FakeRepo());
service.Issue(new Shop.Orders.Order("1"));

class FakeRepo : IRepo
{
    public void Save(Shop.Money.Money money) { }
}
