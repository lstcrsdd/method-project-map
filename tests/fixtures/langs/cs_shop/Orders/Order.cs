namespace Shop.Orders
{
    public record Order(string Id)
    {
        public int Count() => 1;
    }
}
