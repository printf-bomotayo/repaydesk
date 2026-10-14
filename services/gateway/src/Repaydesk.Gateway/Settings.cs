using Npgsql;

namespace Repaydesk.Gateway;

/// <summary>Everything the gateway needs, from the environment compose gives it.</summary>
public sealed record Settings(
    string DatabaseConnection,
    string FineractUrl,
    string FineractTenant,
    string FineractUsername,
    string FineractPassword,
    string OdooUrl,
    string OdooDatabase,
    string OdooUsername,
    string OdooPassword,
    long TwoPersonAboveKobo,
    TimeSpan Poll,
    string MessagingUrl = "http://messaging:8000",
    string MessagingToken = "dev-only-messaging-token")
{
    /// <summary>₦500,000, the same default as the API's (ADR 0005).</summary>
    public const long DefaultTwoPersonAboveKobo = 50_000_000;

    public static Settings FromEnvironment()
    {
        static string Need(string key) =>
            Environment.GetEnvironmentVariable(key) is { Length: > 0 } v ? v : throw new InvalidOperationException($"{key} is not set");
        static string Or(string key, string fallback) =>
            Environment.GetEnvironmentVariable(key) is { Length: > 0 } v ? v : fallback;

        return new Settings(
            ToConnectionString(Need("REPAYDESK_DATABASE_URL")),
            Need("FINERACT_URL"),
            Or("FINERACT_TENANT", "default"),
            Need("FINERACT_USERNAME"),
            Need("FINERACT_PASSWORD"),
            Need("ODOO_URL"),
            Or("ODOO_DB", "ledger"),
            Need("ODOO_USERNAME"),
            Need("ODOO_PASSWORD"),
            long.Parse(Or("TWO_PERSON_ABOVE_KOBO", DefaultTwoPersonAboveKobo.ToString())),
            TimeSpan.FromSeconds(int.Parse(Or("GATEWAY_POLL_SECONDS", "15"))),
            Or("MESSAGING_URL", "http://messaging:8000"),
            Or("MESSAGING_TOKEN", "dev-only-messaging-token"));
    }

    /// <summary>
    /// The services share one database URL, <c>postgresql://user:password@host:port/db?options=...</c>.
    /// Npgsql wants key-value pairs, so this translates. <c>options=-csearch_path=x</c> (how the tests
    /// pick a schema) becomes Npgsql's <c>Search Path</c>.
    /// </summary>
    public static string ToConnectionString(string url)
    {
        if (!url.StartsWith("postgres://") && !url.StartsWith("postgresql://"))
            return url; // already key-value
        var uri = new Uri(url);
        var user = uri.UserInfo.Split(':', 2);
        var b = new NpgsqlConnectionStringBuilder
        {
            Host = uri.Host,
            Port = uri.IsDefaultPort || uri.Port <= 0 ? 5432 : uri.Port,
            Database = Uri.UnescapeDataString(uri.AbsolutePath.TrimStart('/')),
            Username = Uri.UnescapeDataString(user[0]),
            Password = user.Length > 1 ? Uri.UnescapeDataString(user[1]) : null,
        };
        foreach (var pair in uri.Query.TrimStart('?').Split('&', StringSplitOptions.RemoveEmptyEntries))
        {
            var kv = pair.Split('=', 2);
            var value = kv.Length > 1 ? Uri.UnescapeDataString(kv[1]) : "";
            if (kv[0] == "options" && value.StartsWith("-csearch_path="))
                b.SearchPath = value["-csearch_path=".Length..];
            else if (kv[0] == "sslmode")
                b.SslMode = Enum.Parse<SslMode>(value.Replace("-", ""), ignoreCase: true); // verify-full → VerifyFull
        }
        return b.ConnectionString;
    }
}
