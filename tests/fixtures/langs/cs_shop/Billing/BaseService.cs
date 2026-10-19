namespace Shop.Billing;

public abstract class BaseService
{
    public virtual string Describe() => "base";
}

public interface IIssuer
{
    Shop.Money.Money Issue(Shop.Orders.Order order);
}

public interface IRepo
{
    void Save(Shop.Money.Money money);
}

internal static class Helper
{
    public static void Log() { }
}
