// Repaydesk's posting gateway: the only component that writes to core banking and the ledger
// (ADR 0002). It drains posting_outbox, which the API fills when people approve a match.
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Npgsql;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;
using Repaydesk.Gateway;

if (args.Contains("--healthy"))
    return Heartbeat.Healthy(TimeSpan.FromMinutes(2)) ? 0 : 1;

var settings = Settings.FromEnvironment();
var builder = Host.CreateApplicationBuilder(args);
builder.Logging.AddSimpleConsole(o =>
{
    o.SingleLine = true;
    o.TimestampFormat = "yyyy-MM-ddTHH:mm:ss ";
});

static HttpClient Http() => new(new SocketsHttpHandler { PooledConnectionLifetime = TimeSpan.FromMinutes(5) });

builder.Services.AddSingleton(settings);
// Npgsql tries GSS (Kerberos) encryption first by default, which needs a library the runtime image
// doesn't have and a Postgres that isn't configured for it; it says so on every connection.
builder.Services.AddSingleton(_ => NpgsqlDataSource.Create(
    new NpgsqlConnectionStringBuilder(settings.DatabaseConnection) { GssEncryptionMode = GssEncryptionMode.Disable }.ConnectionString));
builder.Services.AddSingleton<ICoreBanking>(_ => new FineractCoreBanking(Http(), settings));
builder.Services.AddSingleton<ILedger>(_ => new OdooLedger(Http(), settings));
builder.Services.AddSingleton<Outbox>();
builder.Services.AddHostedService<Drainer>();

// Traces to Jaeger through the collector when compose sets the endpoint; nothing otherwise.
if (Environment.GetEnvironmentVariable("OTEL_EXPORTER_OTLP_ENDPOINT") is { Length: > 0 })
    builder.Services.AddOpenTelemetry()
        .ConfigureResource(r => r.AddService(Environment.GetEnvironmentVariable("OTEL_SERVICE_NAME") ?? "repaydesk-gateway"))
        .WithTracing(t => t
            .AddSource(Telemetry.Source.Name)
            .AddHttpClientInstrumentation()
            .AddNpgsql()
            .AddOtlpExporter());

await builder.Build().RunAsync();
return 0;
